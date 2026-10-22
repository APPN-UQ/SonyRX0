"""Synthetic end-to-end test: virtual rig (60 mm baseline, 2 deg toe-in) -> calibrate -> depth.

    python tests/synthetic_rig.py [workdir]

Fails (non-zero exit) if the recovered baseline or plane depth is off.
"""
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

import tempfile

from sonyrx0.stereo.common import load_calibration, make_board

WORK = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="rx0synth_"))

W, H = 1800, 1200
K = np.array([[1200, 0, W / 2], [0, 1200, H / 2], [0, 0, 1]], float)
yaw = np.deg2rad(2.0)
R2 = cv2.Rodrigues(np.array([0, yaw, 0.0]))[0]
T2 = np.array([-60.0, 0, 0])  # cam2 sits 60 mm to the right of cam1
CAMS = [(np.eye(3), np.zeros(3)), (R2, T2)]
rng = np.random.default_rng(0)


def render_plane(tex, mm_per_px, Rb, tb, cam):
    R, t = cam
    Rc, tc = R @ Rb, R @ tb + t
    S = np.diag([mm_per_px, mm_per_px, 1.0])
    Hm = K @ np.column_stack([Rc[:, 0], Rc[:, 1], tc]) @ S
    img = cv2.warpPerspective(tex, Hm, (W, H), flags=cv2.INTER_LINEAR, borderValue=(90, 90, 90))
    return cv2.GaussianBlur(img, (3, 3), 0)


# --- calibration shots ---
sq = 24.0
board = make_board(11, 8, sq)
px = 4  # texture px per mm
tex = cv2.cvtColor(board.generateImage((int(11 * sq * px), int(8 * sq * px))), cv2.COLOR_GRAY2BGR)
for side in ("left", "right"):
    (WORK / "calib" / side).mkdir(parents=True, exist_ok=True)
n = 0
while n < 25:
    Rb = cv2.Rodrigues(rng.uniform(-0.5, 0.5, 3))[0]
    z = rng.uniform(500, 1100)
    tb = np.array([rng.uniform(-250, 100), rng.uniform(-150, 150), z])
    tb = tb - Rb @ np.array([11 * sq / 2, 8 * sq / 2, 0])  # centre board at tb
    imgs = [render_plane(tex, 1 / px, Rb, tb, c) for c in CAMS]
    cv2.imwrite(str(WORK / "calib/left" / f"DSC{n:05d}.JPG"), imgs[0])
    cv2.imwrite(str(WORK / "calib/right" / f"DSC{n:05d}.JPG"), imgs[1])
    n += 1

py = sys.executable
subprocess.run([py, "-m", "sonyrx0.stereo.calibrate", "--left", str(WORK / "calib/left"),
                "--right", str(WORK / "calib/right"), "--square-mm", str(sq),
                "--out", str(WORK / "calib.npz")], check=True)

# --- scene: textured fronto-parallel plane at 800 mm ---
Z = 800.0
noise = rng.integers(0, 255, (300, 450, 3), dtype=np.uint8)
noise = cv2.resize(noise, (4500, 3000), interpolation=cv2.INTER_LINEAR)
Rb = np.eye(3)
tb = np.array([-600.0, -400.0, Z])
for side in ("left", "right"):
    (WORK / "scene" / side).mkdir(parents=True, exist_ok=True)
imgs = [render_plane(noise, 1200 / 4500, Rb, tb, c) for c in CAMS]
cv2.imwrite(str(WORK / "scene/left/DSC00100.JPG"), imgs[0], [cv2.IMWRITE_JPEG_QUALITY, 95])
cv2.imwrite(str(WORK / "scene/right/DSC00100.JPG"), imgs[1], [cv2.IMWRITE_JPEG_QUALITY, 95])

subprocess.run([py, "-m", "sonyrx0.stereo.depth", "--calib", str(WORK / "calib.npz"),
                "--left", str(WORK / "scene/left"), "--right", str(WORK / "scene/right"),
                "--min-depth", "500", "--max-depth", "1500", "--out", str(WORK / "out"), "--ply"],
               check=True)
d = np.load(WORK / "out/DSC00100_depth.npy")
h, w = d.shape
centre = d[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
# Plane is fronto-parallel to cam1, but the rectified frame is rotated ~1 deg, so allow a small tilt.
print(f"Centre depth median {np.nanmedian(centre):.1f} mm (truth ~{Z:.0f}), "
      f"IQR {np.nanpercentile(centre, 75) - np.nanpercentile(centre, 25):.1f} mm")

baseline = float(np.linalg.norm(load_calibration(WORK / "calib.npz")["T"]))
ok = abs(baseline - 60.0) < 0.5 and abs(np.nanmedian(centre) - Z) < 10
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
