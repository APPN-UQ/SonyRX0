"""Stereo-calibrate the two RX0 IIs from paired ChArUco board photos.

Example:
    rx0-calibrate --left data/calib/left --right data/calib/right \
        --square-mm 23.9 --out calib.npz

Take ~20-40 triggered pairs with the board at different distances, tilts and
positions, covering the whole frame (especially corners) in both cameras.
Focus must be manual and stay fixed afterwards: refocusing changes the intrinsics.
"""
import argparse

import cv2
import numpy as np

from .common import (DEFAULT_DICT, DEFAULT_SQUARES_X, DEFAULT_SQUARES_Y, load_pairs,
                    make_board, read_image, save_calibration)

MIN_CORNERS = 8


def detect(detector, board, img):
    """Return {charuco_id: (obj_xyz, img_xy)} for one image."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    corners, ids, _, _ = detector.detectBoard(gray)
    if ids is None or len(ids) < MIN_CORNERS:
        return {}
    obj, imgp = board.matchImagePoints(corners, ids)
    return {int(i): (o.reshape(3), p.reshape(2)) for i, o, p in zip(ids.ravel(), obj, imgp)}


def to_arrays(det, ids):
    obj = np.array([det[i][0] for i in ids], np.float32)
    img = np.array([det[i][1] for i in ids], np.float32)
    return obj, img


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--left", required=True)
    ap.add_argument("--right", required=True)
    ap.add_argument("--square-mm", type=float, required=True, help="measured printed square size")
    ap.add_argument("--marker-mm", type=float, help="defaults to 0.75 * square")
    ap.add_argument("--squares-x", type=int, default=DEFAULT_SQUARES_X)
    ap.add_argument("--squares-y", type=int, default=DEFAULT_SQUARES_Y)
    ap.add_argument("--dict", default=DEFAULT_DICT)
    ap.add_argument("--max-pair-error", type=float, default=1.0,
                    help="pairs whose stereo error exceeds this (px, or 2.5x the median) are dropped")
    ap.add_argument("--out", default="calib.npz")
    args = ap.parse_args()

    board = make_board(args.squares_x, args.squares_y, args.square_mm, args.marker_mm, args.dict)
    detector = cv2.aruco.CharucoDetector(board)

    mono = {"L": ([], []), "R": ([], [])}
    stereo_obj, stereo_l, stereo_r, stereo_names = [], [], [], []
    image_size = None

    for lp, rp in load_pairs(args.left, args.right):
        limg, rimg = read_image(lp), read_image(rp)
        size = limg.shape[1::-1]
        if image_size is None:
            image_size = size
        if size != image_size or rimg.shape[1::-1] != image_size:
            raise ValueError(f"Image size mismatch at {lp.name}/{rp.name}; use the same image size on both cameras")

        dl, dr = detect(detector, board, limg), detect(detector, board, rimg)
        for key, det in (("L", dl), ("R", dr)):
            if det:
                o, p = to_arrays(det, sorted(det))
                mono[key][0].append(o)
                mono[key][1].append(p)

        common_ids = sorted(set(dl) & set(dr))
        status = f"{lp.name:>16} {len(dl):3d}L {len(dr):3d}R {len(common_ids):3d} shared"
        if len(common_ids) >= MIN_CORNERS:
            o, pl = to_arrays(dl, common_ids)
            _, pr = to_arrays(dr, common_ids)
            stereo_obj.append(o)
            stereo_l.append(pl)
            stereo_r.append(pr)
            stereo_names.append(lp.name)
        else:
            status += "  (skipped for stereo)"
        print(status)

    if len(stereo_obj) < 5:
        raise SystemExit(f"Only {len(stereo_obj)} usable pairs; need at least 5 (aim for 20+).")

    # Intrinsics per camera first, using every frame that camera saw the board in.
    flags = cv2.CALIB_RATIONAL_MODEL
    rms_l, K1, D1, _, _ = cv2.calibrateCamera(*mono["L"], image_size, None, None, flags=flags)
    rms_r, K2, D2, _, _ = cv2.calibrateCamera(*mono["R"], image_size, None, None, flags=flags)
    print(f"\nIntrinsics RMS: left {rms_l:.3f}px, right {rms_r:.3f}px")

    # Then the extrinsics (R, T between cameras) with intrinsics held fixed.
    # Pairs where the board moved between the two exposures (cameras not perfectly
    # synchronised, hand-held board) disagree with the rest, so drop them iteratively.
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 200, 1e-6)
    keep = list(range(len(stereo_obj)))
    while True:
        R = np.eye(3)
        T = np.zeros((3, 1))
        out = cv2.stereoCalibrateExtended(
            [stereo_obj[i] for i in keep], [stereo_l[i] for i in keep], [stereo_r[i] for i in keep],
            K1, D1, K2, D2, image_size, R, T,
            criteria=criteria, flags=cv2.CALIB_FIX_INTRINSIC | flags)
        rms_s, K1, D1, K2, D2, R, T, E, F = out[:9]
        per_view = out[-1]  # last element in every OpenCV 4.x variant
        errs = per_view.max(axis=1)
        limit = max(args.max_pair_error, 2.5 * float(np.median(errs)))
        bad = [k for k, e in zip(keep, errs) if e > limit]
        if not bad or len(keep) - len(bad) < 10:
            break
        print(f"Stereo RMS {rms_s:.2f}px; dropping {len(bad)} inconsistent pair(s): "
              + ", ".join(f"{stereo_names[k]} ({e:.1f}px)" for k, e in zip(keep, errs) if e > limit))
        keep = [k for k in keep if k not in bad]
    baseline = float(np.linalg.norm(T))
    print(f"Stereo RMS: {rms_s:.3f}px over {len(keep)}/{len(stereo_obj)} pairs, baseline {baseline:.1f} mm")
    if rms_s > 1.0:
        print("  Warning: RMS > 1px. Check for blurry/mis-paired shots, a non-flat board, or a wrong --square-mm.")

    R1, R2, P1, P2, Q, roi1, roi2 = cv2.stereoRectify(
        K1, D1, K2, D2, image_size, R, T, flags=cv2.CALIB_ZERO_DISPARITY, alpha=0)
    if abs(P2[1, 3]) > abs(P2[0, 3]):
        print("  Note: cameras are stacked vertically, so depth.py will match along columns.")

    save_calibration(args.out, K1=K1, D1=D1, K2=K2, D2=D2, R=R, T=T, E=E, F=F,
                     R1=R1, R2=R2, P1=P1, P2=P2, Q=Q,
                     image_size=np.array(image_size), rms=np.array(rms_s))
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
