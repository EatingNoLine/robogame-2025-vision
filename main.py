#!/usr/bin/env python3
"""Command-line entry point for AprilTag detection and pose estimation.

Examples::

    python main.py --source 0
    python main.py --source 20 --calib data/camera_calibration.npz
    python main.py --source assets/test_image.jpg --output assets/test_result.jpg
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import cv2

from config import CAMERA_CONFIG, DETECTOR_CONFIG
from modules.apriltag_detector import AprilTagDetector

IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff"}


def _gui_available() -> bool:
    """Return False on a Linux machine without a display server."""
    if sys.platform.startswith("linux"):
        return bool(
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        )
    return True


class MainController:
    """Run the detector on a camera, a video file, or a single image."""

    def __init__(
        self,
        source: int | str | Path | None = None,
        calib_file: str | Path = DETECTOR_CONFIG["calib_file"],
        tag_family: str = DETECTOR_CONFIG["tag_family"],
        tag_size: float = DETECTOR_CONFIG["tag_size"],
        width: int = CAMERA_CONFIG["width"],
        height: int = CAMERA_CONFIG["height"],
        fps: int = CAMERA_CONFIG["fps"],
        display: bool = True,
        output: str | Path | None = None,
        max_frames: int | None = None,
        verbose: bool = True,
    ) -> None:
        self.source = self._normalise_source(
            CAMERA_CONFIG["device_index"] if source is None else source
        )
        self.calib_file = Path(calib_file)
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.display = bool(display)
        self.output = Path(output) if output else None
        self.max_frames = max_frames
        self.verbose = verbose
        self.window_name = "AprilTag detector"
        self._window_ready = False

        self.detector = AprilTagDetector.from_calibration_file(
            self.calib_file,
            tag_family=tag_family,
            tag_size=tag_size,
        )
        self.last_detections: list[dict[str, Any]] = []

    @staticmethod
    def _normalise_source(source: int | str | Path) -> int | Path:
        if isinstance(source, int):
            return source

        text = str(source).strip()
        if text.isdigit():
            return int(text)

        path = Path(text)
        if not path.is_file():
            raise FileNotFoundError(f"Input source not found: {path}")
        return path

    @property
    def is_image(self) -> bool:
        return isinstance(self.source, Path) and (
            self.source.suffix.lower() in IMAGE_EXTENSIONS
        )

    def process(self, frame: Any) -> tuple[Any, list[dict[str, Any]]]:
        """Detect tags, draw the overlay and return ``(annotated, detections)``."""
        detections = self.detector.detect_tags(frame)
        annotated = self.detector.draw_detections(frame, detections)
        self.last_detections = detections
        return annotated, detections

    def run(self) -> int:
        if self.is_image:
            return self._run_image()
        return self._run_stream()

    def _run_image(self) -> int:
        frame = cv2.imread(str(self.source), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError(f"Could not read image: {self.source}")

        annotated, detections = self.process(frame)
        if self.verbose:
            self._print_detections(detections)

        if self.output is not None:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(self.output), annotated):
                raise RuntimeError(f"Could not write image: {self.output}")
            if self.verbose:
                print(f"Annotated image written to {self.output}")

        if self.display:
            self._show(annotated)
            cv2.waitKey(0)
            cv2.destroyAllWindows()
        return 0

    def _show(self, image: Any) -> None:
        """Display a frame in a resizable, screen-friendly window."""
        if not self._window_ready:
            cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
            height, width = image.shape[:2]
            scale = min(1.0, 1280.0 / width, 720.0 / height)
            cv2.resizeWindow(
                self.window_name,
                max(1, int(width * scale)),
                max(1, int(height * scale)),
            )
            self._window_ready = True
        cv2.imshow(self.window_name, image)

    def _run_stream(self) -> int:
        capture_source = (
            str(self.source) if isinstance(self.source, Path) else self.source
        )
        capture = cv2.VideoCapture(capture_source)
        if not capture.isOpened():
            raise RuntimeError(f"Could not open camera or video: {self.source}")

        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        capture.set(cv2.CAP_PROP_FPS, self.fps)

        total_frames = 0
        window_frames = 0
        fps = 0.0
        last_time = time.perf_counter()
        snapshot_index = 0

        print(
            f"Source: {self.source} | press q or ESC to quit, s to save a frame"
        )
        if not self.display:
            print("Display disabled; running headless")

        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    print("End of stream")
                    break

                annotated, detections = self.process(frame)
                total_frames += 1
                window_frames += 1

                now = time.perf_counter()
                elapsed = now - last_time
                if elapsed >= 0.5:
                    fps = window_frames / elapsed
                    window_frames = 0
                    last_time = now

                cv2.putText(
                    annotated,
                    f"{fps:4.1f} FPS | {len(detections)} tag(s)",
                    (12, 32),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 255, 255),
                    2,
                    cv2.LINE_AA,
                )

                if self.display:
                    self._show(annotated)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (27, ord("q")):
                        break
                    if key == ord("s"):
                        snapshot_index += 1
                        name = self.output or Path(
                            f"snapshot_{snapshot_index:04d}.jpg"
                        )
                        name.parent.mkdir(parents=True, exist_ok=True)
                        if cv2.imwrite(str(name), annotated):
                            print(f"Saved {name}")

                if self.max_frames is not None and total_frames >= self.max_frames:
                    break
        finally:
            capture.release()
            if self.display:
                cv2.destroyAllWindows()

        return 0

    def _print_detections(self, detections: list[dict[str, Any]]) -> None:
        if not detections:
            print("No AprilTags detected")
            return

        for det in detections:
            pose = self.detector.analyze_pose(det["pose"])
            if pose is None:
                print(f"Tag {det['id']}: center={det['center']}")
                continue
            roll, pitch, yaw = pose["euler_angles_deg"]
            print(
                f"Tag {det['id']}: center={det['center']}, "
                f"distance={pose['distance']:.3f} m, "
                f"rpy=({roll:.1f}, {pitch:.1f}, {yaw:.1f}) deg"
            )

    def detections_as_json(self) -> str:
        """Serialise the most recent detections for scripts and tests."""
        payload = []
        for det in self.last_detections:
            pose = self.detector.analyze_pose(det["pose"])
            item: dict[str, Any] = {
                "id": det["id"],
                "center": list(det["center"]),
                "corners": [list(point) for point in det["corners"]],
            }
            if pose is not None:
                item.update(
                    {
                        "distance_m": pose["distance"],
                        "translation_m": list(pose["translation"]),
                        "euler_deg": list(pose["euler_angles_deg"]),
                    }
                )
            payload.append(item)
        return json.dumps(payload, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect AprilTags and estimate their 6-DoF pose.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--source",
        default=None,
        help="camera index (0), video file, or image file",
    )
    parser.add_argument(
        "--calib",
        default=DETECTOR_CONFIG["calib_file"],
        help="camera calibration .npz",
    )
    parser.add_argument(
        "--tag-family",
        default=DETECTOR_CONFIG["tag_family"],
        help="AprilTag family",
    )
    parser.add_argument(
        "--tag-size",
        type=float,
        default=DETECTOR_CONFIG["tag_size"],
        help="edge length of the black tag square in metres",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=CAMERA_CONFIG["width"],
        help="requested capture width",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=CAMERA_CONFIG["height"],
        help="requested capture height",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=CAMERA_CONFIG["fps"],
        help="requested capture frame rate",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="output image (single-image mode) or snapshot path (stream mode)",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="stop a camera/video stream after this many frames",
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="disable GUI windows (headless use)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print detections as JSON (single-image mode)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.tag_size <= 0:
        print("error: --tag-size must be positive", file=sys.stderr)
        return 2

    display = not args.no_display and _gui_available()
    if not args.no_display and not display:
        print("No display server detected; running headless")

    try:
        controller = MainController(
            source=args.source,
            calib_file=args.calib,
            tag_family=args.tag_family,
            tag_size=args.tag_size,
            width=args.width,
            height=args.height,
            fps=args.fps,
            display=display,
            output=args.output,
            max_frames=args.max_frames,
            verbose=not args.json,
        )
        status = controller.run()
        if args.json:
            print(controller.detections_as_json())
        return status
    except (FileNotFoundError, ImportError, KeyError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
