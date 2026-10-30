"""Chessboard camera calibration.

The class collects chessboard corners from a set of images, runs
``cv2.calibrateCamera`` and stores the intrinsic matrix and distortion
coefficients in a small ``.npz`` file that the AprilTag detector can load.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import cv2
import numpy as np


class CameraCalibrator:
    """Calibrate a camera from chessboard images.

    Args:
        chessboard_size: Number of *inner* corners as ``(columns, rows)``.
        square_size: Physical side length of one square, in metres.
    """

    def __init__(
        self,
        chessboard_size: tuple[int, int] = (7, 7),
        square_size: float = 0.01,
    ) -> None:
        self.chessboard_size = tuple(chessboard_size)
        self.square_size = float(square_size)

        self.object_points: list[np.ndarray] = []
        self.image_points: list[np.ndarray] = []

        # Chessboard corners in the board coordinate system, z = 0.
        columns, rows = self.chessboard_size
        template = np.zeros((columns * rows, 3), dtype=np.float32)
        template[:, :2] = (
            np.mgrid[0:columns, 0:rows].T.reshape(-1, 2) * self.square_size
        )
        self.object_template = template

        self.camera_matrix: np.ndarray | None = None
        self.dist_coeffs: np.ndarray | None = None
        self.rvecs: np.ndarray | None = None
        self.tvecs: np.ndarray | None = None
        self.rms: float | None = None
        self.mean_reprojection_error: float | None = None

    def collect_corners(
        self,
        image_paths: Iterable[str | Path],
        show: bool = False,
        delay_ms: int = 200,
    ) -> int:
        """Detect and refine chessboard corners in every readable image.

        Args:
            image_paths: Images to inspect.
            show: Display detected corners (requires a GUI).
            delay_ms: Delay between preview frames in milliseconds.

        Returns:
            Number of images in which a chessboard was found.
        """
        success_count = 0
        show_preview = show

        for image_path in image_paths:
            path = Path(image_path)
            image = cv2.imread(str(path))
            if image is None:
                print(f"[calibrate] cannot read image: {path}")
                continue

            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            found, corners = cv2.findChessboardCorners(
                gray, self.chessboard_size, None
            )
            if not found:
                print(f"[calibrate] chessboard not found: {path.name}")
                continue

            criteria = (
                cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
                30,
                0.001,
            )
            refined = cv2.cornerSubPix(
                gray, corners, (11, 11), (-1, -1), criteria
            )

            self.object_points.append(self.object_template.copy())
            self.image_points.append(refined)
            success_count += 1

            if show_preview:
                preview = cv2.drawChessboardCorners(
                    image.copy(), self.chessboard_size, refined, found
                )
                try:
                    cv2.imshow("Chessboard corners", preview)
                    cv2.waitKey(delay_ms)
                except cv2.error:
                    print("[calibrate] no GUI available; disabling preview")
                    show_preview = False

        if show_preview:
            cv2.destroyAllWindows()

        return success_count

    def calibrate(self, image_size: tuple[int, int]) -> tuple[float, float]:
        """Run the calibration.

        Args:
            image_size: Image size as ``(width, height)`` in pixels.

        Returns:
            ``(rms, mean_reprojection_error)`` in pixels.

        Raises:
            ValueError: If fewer than three usable views were collected.
        """
        if len(self.object_points) < 3:
            raise ValueError(
                "At least three images with a detected chessboard are required"
            )

        rms, matrix, coeffs, rvecs, tvecs = cv2.calibrateCamera(
            self.object_points,
            self.image_points,
            image_size,
            None,
            None,
        )

        self.camera_matrix = matrix
        self.dist_coeffs = coeffs
        self.rvecs = np.asarray(rvecs)
        self.tvecs = np.asarray(tvecs)

        errors: list[float] = []
        for object_point, image_point, rvec, tvec in zip(
            self.object_points, self.image_points, rvecs, tvecs
        ):
            projected, _ = cv2.projectPoints(
                object_point, rvec, tvec, matrix, coeffs
            )
            errors.append(
                float(cv2.norm(image_point, projected, cv2.NORM_L2))
                / float(np.sqrt(len(projected)))
            )

        self.rms = float(rms)
        self.mean_reprojection_error = float(np.mean(errors))
        return self.rms, self.mean_reprojection_error

    def save_calibration(self, output_path: str | Path) -> None:
        """Write intrinsics, distortion and per-view poses to ``output_path``."""
        if self.camera_matrix is None or self.dist_coeffs is None:
            raise ValueError("calibrate() must be called before saving")

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            path,
            camera_matrix=self.camera_matrix,
            dist_coeffs=self.dist_coeffs,
            rvecs=self.rvecs,
            tvecs=self.tvecs,
        )
        print(f"[calibrate] result written to {path}")

    def load_calibration(self, input_path: str | Path) -> None:
        """Load a calibration written by :meth:`save_calibration`."""
        path = Path(input_path)
        if not path.is_file():
            raise FileNotFoundError(f"Calibration file not found: {path}")

        with np.load(path) as data:
            self.camera_matrix = data["camera_matrix"].copy()
            self.dist_coeffs = data["dist_coeffs"].copy()
            self.rvecs = data["rvecs"].copy() if "rvecs" in data else None
            self.tvecs = data["tvecs"].copy() if "tvecs" in data else None

    def undistort_image(self, image: np.ndarray) -> np.ndarray:
        """Undistort ``image`` and crop the invalid black border."""
        if self.camera_matrix is None or self.dist_coeffs is None:
            raise ValueError("load_calibration() or calibrate() must be called first")

        height, width = image.shape[:2]
        new_matrix, roi = cv2.getOptimalNewCameraMatrix(
            self.camera_matrix,
            self.dist_coeffs,
            (width, height),
            1,
            (width, height),
        )
        undistorted = cv2.undistort(
            image,
            self.camera_matrix,
            self.dist_coeffs,
            None,
            new_matrix,
        )

        x, y, w, h = roi
        if w > 0 and h > 0:
            undistorted = undistorted[y : y + h, x : x + w]
        return undistorted
