"""Rectify stereo pairs and compute disparity, depth maps and point clouds.

Examples:
    # one pair
    rx0-depth --calib calib.npz --left L/DSC00012.JPG --right R/DSC00012.JPG \
        --min-depth 400 --max-depth 2000 --out out/
    # whole folders (paired by sorted filename)
    rx0-depth --calib calib.npz --left data/scene/left --right data/scene/right \
        --min-depth 400 --max-depth 2000 --out out/

Outputs per pair (all distances in mm):
    <name>_rectified.jpg   side-by-side check: features should sit on the same line
    <name>_disparity.png   colourised disparity (near = warm)
    <name>_depth.npy       float32 depth, NaN where unknown
    <name>_depth_mm.png    16-bit depth in mm, 0 where unknown
    <name>.ply             coloured point cloud (with --ply)
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

from .common import load_calibration, load_pairs, read_image


class StereoRig:
    def __init__(self, calib_path, scale):
        c = load_calibration(calib_path)
        self.scale = scale
        size = tuple(int(v) for v in c["image_size"])
        self.maps = [
            cv2.initUndistortRectifyMap(c["K1"], c["D1"], c["R1"], c["P1"], size, cv2.CV_16SC2),
            cv2.initUndistortRectifyMap(c["K2"], c["D2"], c["R2"], c["P2"], size, cv2.CV_16SC2),
        ]
        P1, P2 = c["P1"], c["P2"]
        self.f = P1[0, 0]
        self.cx, self.cy = P1[0, 2], P1[1, 2]
        # CALIB_ZERO_DISPARITY puts the baseline in P2's x (side-by-side) or y (stacked) term.
        self.vertical = abs(P2[1, 3]) > abs(P2[0, 3])
        offset = P2[1, 3] if self.vertical else P2[0, 3]
        if offset > 0:
            raise ValueError("The --left camera must be the left (or top) camera; swap --left and --right.")
        self.baseline = -offset / self.f

    def rectify(self, img_l, img_r):
        out = []
        for img, (m1, m2) in zip((img_l, img_r), self.maps):
            r = cv2.remap(img, m1, m2, cv2.INTER_LINEAR)
            if self.scale != 1:
                r = cv2.resize(r, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
            out.append(r)
        return out

    def disparity_range(self, min_depth, max_depth):
        """Disparity search window (in scaled pixels) covering the requested depth range."""
        fb = self.f * self.scale * self.baseline
        d_max = fb / min_depth
        d_min = fb / max_depth if max_depth else 0.0
        min_disp = int(np.floor(d_min))
        num = int(np.ceil((d_max - min_disp) / 16.0)) * 16
        return min_disp, max(num, 16)

    def disparity(self, rect_l, rect_r, min_disp, num_disp, block, use_wls):
        if self.vertical:
            # Transpose so the stacked baseline becomes horizontal for the matcher.
            rect_l, rect_r = cv2.transpose(rect_l), cv2.transpose(rect_r)
        channels = rect_l.shape[2] if rect_l.ndim == 3 else 1
        left = cv2.StereoSGBM_create(
            minDisparity=min_disp, numDisparities=num_disp, blockSize=block,
            P1=8 * channels * block ** 2, P2=32 * channels * block ** 2,
            disp12MaxDiff=1, uniquenessRatio=10, speckleWindowSize=100, speckleRange=2,
            preFilterCap=63, mode=cv2.STEREO_SGBM_MODE_SGBM_3WAY)
        disp_l = left.compute(rect_l, rect_r)
        if use_wls:
            right = cv2.ximgproc.createRightMatcher(left)
            disp_r = right.compute(rect_r, rect_l)
            wls = cv2.ximgproc.createDisparityWLSFilter(left)
            wls.setLambda(8000.0)
            wls.setSigmaColor(1.5)
            disp_l = wls.filter(disp_l, rect_l, disparity_map_right=disp_r)
        disp = disp_l.astype(np.float32) / 16.0  # SGBM output is fixed-point x16
        disp[disp <= min_disp] = np.nan  # out-of-range / unmatched pixels
        return cv2.transpose(disp) if self.vertical else disp

    def to_xyz(self, disp):
        """Back-project scaled-pixel disparity to XYZ (mm) in the left rectified camera frame."""
        s = self.scale
        h, w = disp.shape
        z = (self.f * s * self.baseline) / disp
        u, v = np.meshgrid(np.arange(w, dtype=np.float32) / s, np.arange(h, dtype=np.float32) / s)
        x = (u - self.cx) * z / self.f
        y = (v - self.cy) * z / self.f
        return np.dstack([x, y, z])


def remove_edge_points(disp, max_jump=0.02, window=5):
    """Drop "flying pixels" at depth edges, which sit between foreground and background.

    A pixel is removed if disparity (hence depth) varies by more than max_jump
    (fraction, 0.02 = 2%) within its window. Smooth slopes survive; edges do not.
    """
    valid = np.isfinite(disp)
    kernel = np.ones((window, window), np.uint8)
    dmax = cv2.dilate(np.where(valid, disp, -1e6).astype(np.float32), kernel)
    dmin = cv2.erode(np.where(valid, disp, 1e6).astype(np.float32), kernel)
    out = disp.copy()
    out[valid & ((dmax - dmin) > max_jump * disp)] = np.nan
    return out


def colourise(disp):
    valid = np.isfinite(disp)
    out = np.zeros(disp.shape + (3,), np.uint8)
    if valid.any():
        lo, hi = np.percentile(disp[valid], [1, 99])
        norm = np.clip((disp - lo) / max(hi - lo, 1e-6), 0, 1)
        norm8 = np.nan_to_num(norm * 255).astype(np.uint8)
        out = cv2.applyColorMap(norm8, cv2.COLORMAP_TURBO)
        out[~valid] = 0
    return out


def rectified_check(rect_l, rect_r, vertical):
    pair = np.vstack([rect_l, rect_r]) if vertical else np.hstack([rect_l, rect_r])
    pair = pair.copy()
    step = max(pair.shape[0 if not vertical else 1] // 30, 10)
    if vertical:
        for x in range(0, pair.shape[1], step):
            cv2.line(pair, (x, 0), (x, pair.shape[0] - 1), (0, 255, 0), 1)
    else:
        for y in range(0, pair.shape[0], step):
            cv2.line(pair, (0, y), (pair.shape[1] - 1, y), (0, 255, 0), 1)
    return pair


def write_ply(path, xyz, bgr, max_depth):
    pts = xyz.reshape(-1, 3)
    cols = bgr.reshape(-1, 3)[:, ::-1]
    keep = np.isfinite(pts).all(axis=1)
    if max_depth:
        keep &= pts[:, 2] <= max_depth
    pts, cols = pts[keep].astype(np.float32), cols[keep].astype(np.uint8)
    vertex = np.empty(len(pts), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                                       ("red", "u1"), ("green", "u1"), ("blue", "u1")])
    vertex["x"], vertex["y"], vertex["z"] = pts.T
    vertex["red"], vertex["green"], vertex["blue"] = cols.T
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(vertex)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    )
    with open(path, "wb") as fh:
        fh.write(header.encode("ascii"))
        fh.write(vertex.tobytes())
    return len(vertex)


def compute_depth(rig, img_l, img_r, min_depth, max_depth=None, block=5, wls=False, edge_jump=0.02):
    """Rectify a raw pair and return (rect_l, rect_r, disparity, xyz).

    rect_* are at rig.scale; disparity is in scaled pixels (NaN = unknown);
    xyz is (H, W, 3) in mm in the left rectified camera frame.
    """
    min_disp, num_disp = rig.disparity_range(min_depth, max_depth)
    rect_l, rect_r = rig.rectify(img_l, img_r)
    disp = rig.disparity(rect_l, rect_r, min_disp, num_disp, block, wls)
    if edge_jump > 0:
        disp = remove_edge_points(disp, edge_jump)
    return rect_l, rect_r, disp, rig.to_xyz(disp)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--calib", required=True)
    ap.add_argument("--left", required=True, help="left image or folder")
    ap.add_argument("--right", required=True, help="right image or folder")
    ap.add_argument("--out", default="out")
    ap.add_argument("--min-depth", type=float, required=True, help="nearest distance of interest (mm)")
    ap.add_argument("--max-depth", type=float, help="furthest distance of interest (mm)")
    ap.add_argument("--scale", type=float, default=0.5,
                    help="downscale before matching; 0.5 is a good speed/detail trade-off for 15 MP")
    ap.add_argument("--block", type=int, default=5, help="SGBM block size (odd, 3-11)")
    ap.add_argument("--wls", action="store_true",
                    help="edge-aware WLS smoothing: fills holes for nicer pictures, but blurs thin/edge depths (avoid for measurement)")
    ap.add_argument("--ply", action="store_true", help="also write a coloured point cloud")
    ap.add_argument("--edge-jump", type=float, default=0.02,
                    help="drop pixels whose 5x5 neighbourhood spans more than this fractional depth "
                         "change (removes in-between points at object edges); 0 disables")
    args = ap.parse_args()

    rig = StereoRig(args.calib, args.scale)
    min_disp, num_disp = rig.disparity_range(args.min_depth, args.max_depth)
    print(f"Baseline {rig.baseline:.1f} mm, f {rig.f:.0f} px, "
          f"disparity search {min_disp}..{min_disp + num_disp} px at scale {args.scale}")

    left, right = Path(args.left), Path(args.right)
    pairs = load_pairs(left, right) if left.is_dir() else [(left, right)]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for lp, rp in pairs:
        rect_l, rect_r, disp, xyz = compute_depth(
            rig, read_image(lp), read_image(rp), args.min_depth, args.max_depth,
            args.block, args.wls, args.edge_jump)
        depth = xyz[..., 2]

        stem = out / lp.stem
        cv2.imwrite(f"{stem}_rectified.jpg", rectified_check(rect_l, rect_r, rig.vertical))
        cv2.imwrite(f"{stem}_disparity.png", colourise(disp))
        np.save(f"{stem}_depth.npy", depth.astype(np.float32))
        cv2.imwrite(f"{stem}_depth_mm.png", np.nan_to_num(np.clip(depth, 0, 65535)).astype(np.uint16))
        msg = f"{lp.name}: {np.isfinite(depth).mean() * 100:.0f}% pixels with depth"
        if args.ply:
            n = write_ply(f"{stem}.ply", xyz, rect_l, args.max_depth)
            msg += f", {n} points"
        print(msg)


if __name__ == "__main__":
    main()
