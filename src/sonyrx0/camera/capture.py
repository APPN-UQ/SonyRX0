"""Trigger an RX0 over USB (PC Remote mode) and save the image to the PC.

    rx0-capture --out captures      # one shot
    rx0-capture --props             # dump camera settings
"""
import argparse
import time
from pathlib import Path

from .sony import PROP_NAMES, RX0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="captures")
    ap.add_argument("-n", type=int, default=1, help="number of shots")
    ap.add_argument("--props", action="store_true", help="print camera settings and exit")
    args = ap.parse_args()

    cam = RX0()
    try:
        if args.props:
            for code, p in sorted(cam.props().items()):
                name = PROP_NAMES.get(code, "")
                form = p["form"][0] if p["form"] else ""
                print(f"0x{code:04X} {name:<26} = {p['current']!r:<14} {'rw' if p['writable'] else 'ro'} {form}")
            return
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for i in range(args.n):
            t0 = time.time()
            for name, data in cam.capture():
                path = out / f"{time.strftime('%Y%m%d_%H%M%S')}_{name}"
                path.write_bytes(data)
                print(f"saved {path} ({len(data) / 1e6:.1f} MB, {time.time() - t0:.1f}s)")
    finally:
        cam.close()


if __name__ == "__main__":
    main()
