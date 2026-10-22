"""Show or change settings on every connected RX0 at once (PC Remote mode).

    rx0-settings                                   # show settings
    rx0-settings --mode m --shutter 1/500 --iso 400 --focus manual --wb daylight
    rx0-settings --mode auto --iso auto            # back to auto

    rx0-settings --focus-lock                      # AF on what's in front, then lock (MF)

Order matters: mode is applied first, because it changes which values are allowed.
Shutter/ISO only stick in M mode (iso also in P). For --wb kelvin, add --kelvin 5600.
"""
import argparse
import sys
import time

from .sony import RX0, SETTINGS
from .wpd_mtp import find_devices

ORDER = ["mode", "focus", "wb", "kelvin", "shutter", "iso", "ev", "size"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ORDER:
        ap.add_argument(f"--{name}")
    ap.add_argument("--focus-lock", action="store_true",
                    help="autofocus on the subject now, then switch to manual so focus stays put")
    args = ap.parse_args()
    changes = [(n, getattr(args, n)) for n in ORDER if getattr(args, n) is not None]
    assert set(ORDER) == set(SETTINGS)

    failed = False
    paths = find_devices()
    if not paths:
        raise SystemExit("No Sony camera found in PC Remote mode.")
    for path in paths:
        serial = path.split("#")[2]
        cam = RX0(path)
        try:
            for name, value in changes:
                cam.apply(name, value)
            if args.focus_lock:
                print(f"{serial}: focus indication during AF {cam.focus_lock()}")
            if changes:
                time.sleep(1.0)
            s = cam.get_settings()
            print(f"{serial}: " + "  ".join(f"{n}={s.get(n, '?')}" for n in ORDER))
            wrong = [f"{n} is {s.get(n)} (wanted {v})" for n, v in changes
                     if s.get(n) != SETTINGS[n][2](SETTINGS[n][1](v))]
            if wrong:
                failed = True
                print(f"  WARNING {serial}: " + "; ".join(wrong))
        finally:
            cam.close()
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
