#!/usr/bin/env python3
"""Run chessboard camera calibration and write a ``.npz`` file.

Example::

    python scripts/calibrate.py \
        --images data/calibration_images/1920_1080 \
        --cols 7 --rows 7 --square-size 0.01 \
        --output data/camera_calibration.npz

Run this from the repository root so that the default paths resolve.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.camera_calibration import CameraCalibrator  # noqa: E402

IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calibrate a camera from chessboard images.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--images",
        default="data/calibration_images/1920_1080",
        help="directory containing chessboard images",
    )
    parser.add_argument(
        "--output",
        default="data/camera_calibration.npz",
        help="output calibration file",
    )
    parser.add_argument(
        "--cols",
        type=int,
        default=7,
        help="number of inner corners along the board width",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=7,
        help="number of inner corners along the board height",
    )
    parser.add_argument(
        "--square-size",
        type=float,
        default=0.01,
        help="physical square size in metres",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="show detected corners (requires a GUI)",
    )
    return parser.parse_args(argv)


def find_images(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Image directory not found: {directory}")
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.cols < 2 or args.rows < 2:
        print("error: --cols and --rows must be at least 2", file=sys.stderr)
        return 2
    if args.square_size <= 0:
        print("error: --square-size must be positive", file=sys.stderr)
        return 2

    try:
        images = find_images(Path(args.images))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not images:
        print(f"error: no images found in {args.images}", file=sys.stderr)
        return 1

    print(f"[calibrate] found {len(images)} images in {args.images}")
    calibrator = CameraCalibrator((args.cols, args.rows), args.square_size)
    usable = calibrator.collect_corners(images, show=args.show)
    print(f"[calibrate] chessboard detected in {usable}/{len(images)} images")

    if usable < 3:
        print(
            "error: at least three usable views are required; "
            "check the board size, lighting and focus",
            file=sys.stderr,
        )
        return 1

    sample = None
    for path in images:
        sample = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if sample is not None:
            break
    if sample is None:
        print("error: could not read any image", file=sys.stderr)
        return 1

    image_size = (sample.shape[1], sample.shape[0])
    try:
        rms, mean_error = calibrator.calibrate(image_size)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"[calibrate] image size: {image_size[0]}x{image_size[1]}")
    print(f"[calibrate] RMS reprojection error: {rms:.4f} px")
    print(f"[calibrate] mean per-view RMS error: {mean_error:.4f} px")
    print("[calibrate] camera matrix:")
    print(calibrator.camera_matrix)
    print("[calibrate] distortion coefficients:")
    print(calibrator.dist_coeffs.ravel())

    calibrator.save_calibration(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
