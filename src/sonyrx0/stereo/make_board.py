"""Generate a ChArUco calibration board, for printing or for a tablet screen.

Print:
    rx0-board --square-mm 24 --out board_a4.png
    Print at 100% scale (no "fit to page") and stick it to something rigid.

Tablet / screen:
    rx0-board --screen 2560x1600 --out board_2560x1600.png
    Uses the screen's exact pixel size with whole pixels per square, so nothing
    gets resampled. Show it full screen with no zoom.

Either way, MEASURE the board with a ruler and pass the square size to
calibrate.py: measure across all the squares and divide, for accuracy.
"""
import argparse

import cv2
import numpy as np

from .common import DEFAULT_DICT, DEFAULT_SQUARES_X, DEFAULT_SQUARES_Y, make_board


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--squares-x", type=int, default=DEFAULT_SQUARES_X)
    ap.add_argument("--squares-y", type=int, default=DEFAULT_SQUARES_Y)
    ap.add_argument("--square-mm", type=float, default=24.0, help="print mode: square size")
    ap.add_argument("--dpi", type=int, default=300, help="print mode: resolution")
    ap.add_argument("--screen", help="screen mode: exact screen resolution, e.g. 2560x1600")
    ap.add_argument("--dict", default=DEFAULT_DICT)
    ap.add_argument("--out", default="charuco_board.png")
    args = ap.parse_args()

    if args.screen:
        sw, sh = (int(v) for v in args.screen.lower().split("x"))
        if sh > sw:
            sw, sh = sh, sw  # build landscape; rotate the tablet to match
        margin = max(sw, sh) // 40  # quiet white border helps detection near the bezel
        sq_px = min((sw - 2 * margin) // args.squares_x, (sh - 2 * margin) // args.squares_y)
        board = make_board(args.squares_x, args.squares_y, float(sq_px), dict_name=args.dict)
        bw, bh = sq_px * args.squares_x, sq_px * args.squares_y
        img = np.full((sh, sw), 255, np.uint8)
        y0, x0 = (sh - bh) // 2, (sw - bw) // 2
        img[y0:y0 + bh, x0:x0 + bw] = board.generateImage((bw, bh), marginSize=0)
        cv2.imwrite(args.out, img)
        print(f"Wrote {args.out}: {sw}x{sh} px, {args.squares_x}x{args.squares_y} squares of {sq_px} px. "
              "Show full screen (landscape), then measure the squares in mm.")
        return

    board = make_board(args.squares_x, args.squares_y, args.square_mm, dict_name=args.dict)
    px_per_mm = args.dpi / 25.4
    size = (round(args.squares_x * args.square_mm * px_per_mm),
            round(args.squares_y * args.square_mm * px_per_mm))
    img = board.generateImage(size, marginSize=round(5 * px_per_mm))
    cv2.imwrite(args.out, img)
    w_mm = args.squares_x * args.square_mm
    h_mm = args.squares_y * args.square_mm
    print(f"Wrote {args.out}: {w_mm:.0f} x {h_mm:.0f} mm board at {args.dpi} dpi. Print at 100% scale.")


if __name__ == "__main__":
    main()
