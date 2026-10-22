"""Fire both RX0s over USB at (nearly) the same moment and save the pair.

    rx0-stereo --out data/scene          # one pair
    rx0-stereo --out data/calib -n 30 --interval 2
    rx0-stereo --swap                    # if left/right are the wrong way round
    rx0-stereo --out data/calib -n 30 --board --beep   # calibration session

--beep sounds the moment both shutters fire (move the board after it).
--board reports how many ChArUco corners each camera saw in every pair.

Images go to <out>/left and <out>/right with matching names, ready for
rx0-calibrate and rx0-depth. Which serial is "left" is remembered in a rig file
(default ./rig.json in the current folder, so each project keeps its own).
"""
import argparse
import json
import queue
import re
import threading
import time
from pathlib import Path

import comtypes
import cv2
import numpy as np

from .sony import RX0
from .wpd_mtp import find_devices

from ..stereo.common import make_board


def board_corners(detector, jpeg):
    """ChArUco corners found in a JPEG, decoded at half size for speed."""
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_REDUCED_GRAYSCALE_2)
    _, ids, _, _ = detector.detectBoard(img)
    return 0 if ids is None else len(ids)


def beep():
    import winsound
    threading.Thread(target=winsound.Beep, args=(1500, 150), daemon=True).start()


def serial_of(path):
    return re.search(r"#([^#]+)#\{", path).group(1)


class CameraWorker(threading.Thread):
    """Owns one camera; COM objects must stay on the thread that created them."""

    def __init__(self, path, barrier):
        super().__init__(daemon=True)
        self.path, self.serial, self.barrier = path, serial_of(path), barrier
        self.jobs, self.results = queue.Queue(), queue.Queue()
        self.start()
        ok, err = self.results.get()
        if not ok:
            raise err

    def run(self):
        comtypes.CoInitialize()
        try:
            cam = RX0(self.path)
            if n := cam.discard_pending():
                print(f"  {self.serial}: discarded {n} leftover image(s)")
        except Exception as e:  # noqa: BLE001 - hand the error to the main thread
            self.results.put((False, e))
            return
        self.results.put((True, None))
        while (job := self.jobs.get()) is not None:
            try:
                cam.trigger_prepare()
                self.barrier.wait()
                t_fire = time.perf_counter()
                cam.trigger_release()
                files = []
                pending = cam.wait_for_image()
                while pending:
                    files.append(cam.download_image())
                    time.sleep(0.2)
                    pending = cam.pending_images()
                self.results.put((True, (t_fire, files)))
            except Exception as e:  # noqa: BLE001
                self.barrier.abort()
                self.results.put((False, e))
        cam.close()

    def shoot(self):
        self.jobs.put("shoot")

    def result(self):
        ok, value = self.results.get()
        if not ok:
            raise value
        return value

    def stop(self):
        self.jobs.put(None)


def load_rig(rig_file, serials, swap):
    rig_file = Path(rig_file)
    rig = json.loads(rig_file.read_text()) if rig_file.exists() else {}
    if {rig.get("left"), rig.get("right")} != set(serials):
        rig = {"left": serials[0], "right": serials[1]}
    if swap:
        rig = {"left": rig["right"], "right": rig["left"]}
    rig_file.write_text(json.dumps(rig, indent=2))
    return rig


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="captures/stereo")
    ap.add_argument("-n", type=int, default=1, help="number of pairs")
    ap.add_argument("--interval", type=float, default=1.0, help="extra seconds between pairs")
    ap.add_argument("--swap", action="store_true", help="swap which camera is left/right (remembered)")
    ap.add_argument("--rig", default="rig.json", help="file remembering which camera serial is left/right")
    ap.add_argument("--beep", action="store_true", help="beep when the shutters fire")
    ap.add_argument("--board", action="store_true", help="report ChArUco corners seen in each pair")
    args = ap.parse_args()

    paths = find_devices()
    if len(paths) != 2:
        raise SystemExit(f"Found {len(paths)} Sony cameras; need exactly 2 in PC Remote mode.")
    by_serial = {serial_of(p): p for p in paths}
    rig = load_rig(args.rig, sorted(by_serial), args.swap)
    print(f"left = {rig['left']}, right = {rig['right']}")

    barrier = threading.Barrier(2, action=beep if args.beep else None)
    detector = cv2.aruco.CharucoDetector(make_board(11, 8, 1.0)) if args.board else None
    workers = {side: CameraWorker(by_serial[rig[side]], barrier) for side in ("left", "right")}
    out = Path(args.out)
    for side in workers:
        (out / side).mkdir(parents=True, exist_ok=True)

    try:
        for i in range(args.n):
            if i:
                time.sleep(args.interval)
            barrier.reset()
            for w in workers.values():
                w.shoot()
            results = {side: w.result() for side, w in workers.items()}
            stamp = time.strftime("%Y%m%d_%H%M%S")
            skew_ms = abs(results["left"][0] - results["right"][0]) * 1000
            for side, (_, files) in results.items():
                for name, data in files:
                    path = out / side / f"{stamp}_{i:03d}{Path(name).suffix}"
                    path.write_bytes(data)
                    print(f"  {side:<5} {path} ({len(data) / 1e6:.1f} MB)")
            msg = f"pair {i + 1}/{args.n}: shutter commands sent {skew_ms:.1f} ms apart"
            if detector:
                seen = {side: board_corners(detector, files[0][1]) for side, (_, files) in results.items()}
                msg += f" | board corners L {seen['left']}/70  R {seen['right']}/70"
                if min(seen.values()) < 8:
                    msg += "  <-- board not usable in this pair"
            print(msg, flush=True)
    finally:
        for w in workers.values():
            w.stop()


if __name__ == "__main__":
    main()
