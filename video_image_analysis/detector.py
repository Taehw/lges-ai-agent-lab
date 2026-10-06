from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

MODEL_PATH = Path(__file__).resolve().parent / "yolo11n.pt"
CONF_THRESHOLD = 0.25

_model = None


def get_model():
    global _model
    if _model is None:
        _model = YOLO(str(MODEL_PATH))
    return _model


def _color_for(class_id):
    rng = np.random.default_rng(class_id)
    return tuple(int(c) for c in rng.integers(60, 256, size=3))


def draw_detections(frame_bgr, result):
    names = result.names
    boxes = result.boxes
    if boxes is None:
        return frame_bgr

    thickness = max(2, round(sum(frame_bgr.shape[:2]) / 600))
    font_scale = thickness / 3

    for xyxy, conf, cls in zip(boxes.xyxy.tolist(), boxes.conf.tolist(), boxes.cls.tolist()):
        x1, y1, x2, y2 = map(int, xyxy)
        class_id = int(cls)
        color = _color_for(class_id)
        label = f"{names[class_id]} {conf:.2f}"

        cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)

        (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
        top = y1 - th - baseline - 4
        if top < 0:
            top = y1
        left = max(0, min(x1, frame_bgr.shape[1] - tw - 6))
        cv2.rectangle(frame_bgr, (left, top), (left + tw + 6, top + th + baseline + 4), color, -1)
        cv2.putText(
            frame_bgr,
            label,
            (left + 3, top + th + 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )
    return frame_bgr


def detect(image_rgb):
    """RGB 이미지를 받아 박스가 그려진 RGB 이미지를 돌려준다."""
    if image_rgb is None:
        return None
    frame_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    result = get_model().predict(frame_bgr, conf=CONF_THRESHOLD, verbose=False)[0]
    frame_bgr = draw_detections(frame_bgr, result)
    return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
