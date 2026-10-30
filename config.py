"""Default configuration for the vision pipeline.

Values can be overridden from the command line, for example::

    python main.py --source 0 --tag-size 0.03
"""

# Camera or video source used when ``--source`` is not supplied.
# A laptop normally uses 0; the Orange Pi 5 Plus setup used 20 and 21.
CAMERA_CONFIG = {
    "device_index": 0,
    "width": 1920,
    "height": 1080,
    "fps": 30,
}

# AprilTag detector defaults.
DETECTOR_CONFIG = {
    "calib_file": "data/camera_calibration.npz",
    "tag_family": "tag36h11",
    # Edge length of the black tag square, in metres.
    "tag_size": 0.03,
}
