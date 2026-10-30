"""AprilTag detection and 6-DoF pose estimation.

The detector supports both common Python AprilTag backends:

* ``pupil-apriltags`` (prebuilt wheels, recommended on desktop platforms)
* ``apriltag`` (the original SWATbotics wrapper, often used on Linux/ARM)

Only one of them needs to be installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np


def _import_backend() -> Any:
    """Import whichever AprilTag Python binding is available."""
    try:
        import pupil_apriltags as backend  # type: ignore[import-not-found]

        return backend
    except ImportError:
        pass

    try:
        import apriltag as backend  # type: ignore[import-not-found]

        return backend
    except ImportError as exc:
        raise ImportError(
            "No AprilTag backend is installed. Install one of:\n"
            "  pip install pupil-apriltags   # prebuilt wheels\n"
            "  pip install apriltag          # source build, common on ARM Linux"
        ) from exc


class AprilTagDetector:
    """Detect AprilTags and estimate their pose relative to the camera.

    Args:
        tag_family: AprilTag family, e.g. ``"tag36h11"``.
        tag_size: Edge length of the black tag square, in metres.
        camera_matrix: 3x3 camera intrinsic matrix. If omitted, poses are not
            computed and only the 2D tag geometry is returned.
        dist_coeffs: Camera distortion coefficients.
    """

    def __init__(
        self,
        tag_family: str = "tag36h11",
        tag_size: float = 0.03,
        camera_matrix: np.ndarray | None = None,
        dist_coeffs: np.ndarray | None = None,
    ) -> None:
        backend = _import_backend()

        # pupil-apriltags takes keyword arguments; the SWATbotics wrapper uses
        # a DetectorOptions object.
        if hasattr(backend, "DetectorOptions"):
            options = backend.DetectorOptions(families=tag_family)
            self.detector = backend.Detector(options)
        else:
            self.detector = backend.Detector(families=tag_family)

        self.tag_family = tag_family
        self.tag_size = float(tag_size)
        self.camera_matrix = (
            np.asarray(camera_matrix, dtype=np.float64)
            if camera_matrix is not None
            else None
        )
        self.dist_coeffs = (
            np.asarray(dist_coeffs, dtype=np.float64).reshape(-1, 1)
            if dist_coeffs is not None
            else np.zeros((5, 1), dtype=np.float64)
        )

        if self.camera_matrix is not None:
            if self.camera_matrix.shape != (3, 3):
                raise ValueError("camera_matrix must be a 3x3 matrix")
            self.fx = float(self.camera_matrix[0, 0])
            self.fy = float(self.camera_matrix[1, 1])
            self.cx = float(self.camera_matrix[0, 2])
            self.cy = float(self.camera_matrix[1, 2])
        else:
            self.fx = self.fy = self.cx = self.cy = None

        # 3-D coordinates of the four tag corners, centred on the tag.
        half = self.tag_size / 2.0
        self.tag_3d_points = np.array(
            [
                [-half, -half, 0.0],
                [half, -half, 0.0],
                [half, half, 0.0],
                [-half, half, 0.0],
            ],
            dtype=np.float32,
        )

    @classmethod
    def from_calibration_file(
        cls,
        calib_file: str | Path,
        tag_family: str = "tag36h11",
        tag_size: float = 0.03,
    ) -> "AprilTagDetector":
        """Create a detector from a ``.npz`` file written by the calibration script."""
        path = Path(calib_file)
        if not path.is_file():
            raise FileNotFoundError(f"Calibration file not found: {path}")

        with np.load(path) as data:
            missing = {"camera_matrix", "dist_coeffs"} - set(data.files)
            if missing:
                raise KeyError(
                    f"{path} is missing {sorted(missing)}; "
                    "regenerate it with scripts/calibrate.py"
                )
            camera_matrix = data["camera_matrix"].copy()
            dist_coeffs = data["dist_coeffs"].copy()

        return cls(tag_family, tag_size, camera_matrix, dist_coeffs)

    def detect_tags(self, image: np.ndarray) -> list[dict[str, Any]]:
        """Detect tags in a BGR or grayscale image.

        Returns one dict per tag with ``id``, ``center``, ``corners`` and
        ``pose``. ``pose`` is ``None`` when no calibration was supplied.
        """
        if image is None:
            raise ValueError("image is None")

        if image.ndim == 3:
            code = (
                cv2.COLOR_BGR2GRAY
                if image.shape[2] == 3
                else cv2.COLOR_BGRA2GRAY
            )
            gray = cv2.cvtColor(image, code)
        else:
            gray = image
        if gray.dtype != np.uint8:
            gray = cv2.convertScaleAbs(gray)

        detections = self.detector.detect(gray)
        results: list[dict[str, Any]] = []

        for det in detections:
            corners_float = np.asarray(det.corners, dtype=np.float32)
            pose = None

            if self.camera_matrix is not None:
                # ITERATIVE handles the corner ordering returned by both
                # AprilTag backends; IPPE_SQUARE requires a stricter order.
                ok, rvec, tvec = cv2.solvePnP(
                    objectPoints=self.tag_3d_points,
                    imagePoints=corners_float,
                    cameraMatrix=self.camera_matrix,
                    distCoeffs=self.dist_coeffs,
                    flags=cv2.SOLVEPNP_ITERATIVE,
                )
                if ok:
                    pose = {"rvec": rvec, "tvec": tvec}

            results.append(
                {
                    "id": int(det.tag_id),
                    "center": tuple(int(round(v)) for v in det.center),
                    "corners": [
                        (int(round(x)), int(round(y))) for x, y in corners_float
                    ],
                    "pose": pose,
                }
            )

        return results

    def draw_detections(
        self, image: np.ndarray, detections: list[dict[str, Any]]
    ) -> np.ndarray:
        """Return a copy of ``image`` with tag outlines, IDs and pose axes."""
        output = image.copy()

        for det in detections:
            corners = det["corners"]
            center = det["center"]
            pose = det["pose"]

            for index in range(4):
                cv2.line(
                    output,
                    corners[index],
                    corners[(index + 1) % 4],
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )
            cv2.circle(output, center, 4, (0, 0, 255), -1, cv2.LINE_AA)

            if pose is not None and self.camera_matrix is not None:
                cv2.drawFrameAxes(
                    output,
                    self.camera_matrix,
                    self.dist_coeffs,
                    pose["rvec"],
                    pose["tvec"],
                    self.tag_size / 2.0,
                    2,
                )

            label = f"ID {det['id']}"
            pose_info = self.analyze_pose(pose)
            if pose_info is not None:
                label += f"  {pose_info['distance'] * 100.0:.1f} cm"

            (text_width, text_height), baseline = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2
            )
            min_x = min(point[0] for point in corners)
            min_y = min(point[1] for point in corners)
            origin = (
                max(min_x, 4),
                max(min_y - 10, text_height + 4),
            )
            cv2.rectangle(
                output,
                (origin[0] - 4, origin[1] - text_height - 4),
                (origin[0] + text_width + 4, origin[1] + baseline),
                (0, 0, 0),
                -1,
            )
            cv2.putText(
                output,
                label,
                origin,
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

        return output

    def analyze_pose(self, pose: dict[str, np.ndarray] | None) -> dict[str, Any] | None:
        """Convert a pose returned by :meth:`detect_tags` into readable values.

        Euler angles use the Z-Y-X convention and are returned in radians and
        degrees as ``(roll, pitch, yaw)``.
        """
        if pose is None:
            return None

        rotation_matrix, _ = cv2.Rodrigues(pose["rvec"])
        sy = float(np.hypot(rotation_matrix[0, 0], rotation_matrix[1, 0]))
        singular = sy < 1e-6

        if not singular:
            roll = float(np.arctan2(rotation_matrix[2, 1], rotation_matrix[2, 2]))
            pitch = float(np.arctan2(-rotation_matrix[2, 0], sy))
            yaw = float(np.arctan2(rotation_matrix[1, 0], rotation_matrix[0, 0]))
        else:
            roll = float(np.arctan2(-rotation_matrix[1, 2], rotation_matrix[1, 1]))
            pitch = float(np.arctan2(-rotation_matrix[2, 0], sy))
            yaw = 0.0

        translation = np.asarray(pose["tvec"], dtype=np.float64).reshape(3)
        euler = (roll, pitch, yaw)

        return {
            "rotation_matrix": rotation_matrix,
            "euler_angles": euler,
            "euler_angles_deg": tuple(float(v) for v in np.degrees(euler)),
            "distance": float(np.linalg.norm(translation)),
            "translation": tuple(float(v) for v in translation),
        }
