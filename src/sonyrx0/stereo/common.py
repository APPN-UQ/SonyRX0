"""Shared helpers: ChArUco board definition and stereo image-pair loading."""
from pathlib import Path

import cv2
import numpy as np

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

# Default board: 11 x 8 squares fits on A4 at 24 mm squares (264 x 192 mm).
DEFAULT_SQUARES_X = 11
DEFAULT_SQUARES_Y = 8
DEFAULT_DICT = "DICT_5X5_100"


def make_board(squares_x, squares_y, square_mm, marker_mm=None, dict_name=DEFAULT_DICT):
    """Build a ChArUco board. Lengths are in mm, so all 3D output is in mm."""
    if marker_mm is None:
        marker_mm = square_mm * 0.75
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dict_name))
    return cv2.aruco.CharucoBoard((squares_x, squares_y), square_mm, marker_mm, dictionary)


def read_image(path):
    # Ignore EXIF rotation so both cameras are always in raw sensor orientation.
    img = cv2.imread(str(path), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    if img is None:
        raise IOError(f"Could not read {path}")
    return img


def list_images(folder):
    return sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in IMAGE_EXTS)


def load_pairs(left_dir, right_dir):
    """Pair images by sorted filename order.

    With a shared hardware trigger both cameras fire together, so the Nth file
    on each card is the same moment, provided both cards started from the same
    state. A count mismatch means a missed frame, so refuse rather than mis-pair.
    """
    left, right = list_images(left_dir), list_images(right_dir)
    if len(left) != len(right):
        raise ValueError(
            f"{len(left)} left vs {len(right)} right images. A camera missed a "
            "trigger; remove the unmatched frames so the folders line up."
        )
    if not left:
        raise ValueError(f"No images found in {left_dir}")
    return list(zip(left, right))


def save_calibration(path, **arrays):
    np.savez(path, **arrays)


def load_calibration(path):
    data = np.load(path)
    return {k: data[k] for k in data.files}
