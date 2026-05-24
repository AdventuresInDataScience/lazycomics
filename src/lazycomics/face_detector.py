"""Face detection — hybrid YuNet + MediaPipe.

A single source of truth for face detection across the package. Used by:

* ``ref_preparer`` — face-aware crop of the primary reference image.
* ``text_renderer`` — face-aware bubble placement and tail anchoring.

Why a hybrid: empirically, neither MediaPipe nor YuNet alone catches every
face in stylised AI-generated comic panels. MediaPipe's ``face_detection``
short-range model handles close-up faces filling most of the frame, but
fails on smaller / stylised faces (e.g. a cyberpunk character at medium
distance). YuNet (OpenCV's lightweight ONNX detector) handles those
medium / multi-character / stylised cases reliably, but tends to miss the
huge-closeup case that MediaPipe gets. Running both and merging gives
near-perfect coverage on the comic-art panels this package generates.

Public API:

* ``detect_faces(image) -> list[FaceBox]`` — full bboxes, one per face.
* ``detect_face_centre(image) -> tuple[int, int] | None`` — convenience
  for callers that only need the most-confident face's centre.

A ``FaceBox`` is ``(x0, y0, x1, y1, score)`` in pixel coordinates.

YuNet model file ships as a package asset
(``assets/models/face_detection_yunet_2023mar.onnx``, ~230 KB). MediaPipe
is a required dep already; OpenCV ships with ``cv2.FaceDetectorYN``
support out of the box on opencv-contrib (which mediapipe pulls in).
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import cv2
import mediapipe as mp
import numpy as np
from PIL import Image

__all__ = ["FaceBox", "detect_faces", "detect_face_centre"]


_YUNET_MODEL_PATH = Path(__file__).parent / "assets" / "models" / "face_detection_yunet_2023mar.onnx"
_YUNET_SCORE_THRESH = 0.5
_MEDIAPIPE_MIN_CONF = 0.5
_DEDUP_IOU_THRESHOLD = 0.4


class FaceBox(NamedTuple):
    """Pixel-space face bbox + detection score."""
    x0: int
    y0: int
    x1: int
    y1: int
    score: float


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_faces(img: Image.Image) -> list[FaceBox]:
    """Return all faces detected in ``img``, deduped across both detectors.

    Empty list on no detections or detector error (errors logged loudly so
    the user notices a misconfiguration rather than discovering missing
    tails later).
    """
    yunet_boxes = _detect_yunet(img)
    mp_boxes = _detect_mediapipe(img)
    return _merge_and_dedup(yunet_boxes + mp_boxes)


def detect_face_centre(img: Image.Image) -> tuple[int, int] | None:
    """Centre of the highest-scoring face, or ``None`` when nothing detected.

    Convenience for callers that just need a single anchor point (e.g.
    face-aware cropping). Use ``detect_faces`` when you need all faces.
    """
    faces = detect_faces(img)
    if not faces:
        return None
    best = max(faces, key=lambda f: f.score)
    return ((best.x0 + best.x1) // 2, (best.y0 + best.y1) // 2)


# ---------------------------------------------------------------------------
# YuNet (OpenCV) — primary detector
# ---------------------------------------------------------------------------


def _detect_yunet(img: Image.Image) -> list[FaceBox]:
    """Run OpenCV's YuNet on ``img``. Returns empty list on failure."""
    if not _YUNET_MODEL_PATH.is_file():
        print(
            f"[face_detector] YuNet model missing at {_YUNET_MODEL_PATH}; "
            "skipping YuNet detection. Reinstall the package or restore the "
            "model file."
        )
        return []
    try:
        arr = np.array(img.convert("RGB"))
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]
        detector = cv2.FaceDetectorYN.create(
            str(_YUNET_MODEL_PATH), "", (w, h),
            _YUNET_SCORE_THRESH, 0.3, 5000,
        )
        _, faces = detector.detect(bgr)
    except Exception as e:
        print(f"[face_detector] YuNet error: {e}")
        return []

    if faces is None:
        return []

    out: list[FaceBox] = []
    for face in faces:
        x, y, fw, fh = face[:4]
        score = float(face[-1])
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(w, int(x + fw))
        y1 = min(h, int(y + fh))
        if x1 > x0 and y1 > y0:
            out.append(FaceBox(x0, y0, x1, y1, score))
    return out


# ---------------------------------------------------------------------------
# MediaPipe — fills in the giant-closeup case YuNet misses
# ---------------------------------------------------------------------------


def _detect_mediapipe(img: Image.Image) -> list[FaceBox]:
    """Run MediaPipe short-range face_detection. Returns empty on failure."""
    try:
        detector = mp.solutions.face_detection.FaceDetection(
            model_selection=0, min_detection_confidence=_MEDIAPIPE_MIN_CONF,
        )
        arr = np.array(img.convert("RGB"))
        results = detector.process(arr)
    except Exception as e:
        print(f"[face_detector] MediaPipe error: {e}")
        return []

    if not results.detections:
        return []

    w, h = img.size
    out: list[FaceBox] = []
    for det in results.detections:
        bbox = det.location_data.relative_bounding_box
        x0 = max(0, int(bbox.xmin * w))
        y0 = max(0, int(bbox.ymin * h))
        x1 = min(w, int((bbox.xmin + bbox.width) * w))
        y1 = min(h, int((bbox.ymin + bbox.height) * h))
        if x1 > x0 and y1 > y0:
            out.append(FaceBox(x0, y0, x1, y1, float(det.score[0])))
    return out


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


def _merge_and_dedup(boxes: list[FaceBox]) -> list[FaceBox]:
    """Drop overlapping detections (IoU > threshold), keep highest score.

    Two detectors disagree slightly on bbox edges even when they find the
    same face; we don't want to count it twice in downstream face-counting
    logic.
    """
    if not boxes:
        return []
    # Sort by score descending so we keep the highest-scoring of each pair.
    ordered = sorted(boxes, key=lambda b: b.score, reverse=True)
    kept: list[FaceBox] = []
    for box in ordered:
        if any(_iou(box, k) > _DEDUP_IOU_THRESHOLD for k in kept):
            continue
        kept.append(box)
    return kept


def _iou(a: FaceBox, b: FaceBox) -> float:
    """Intersection-over-union of two bboxes."""
    ix0 = max(a.x0, b.x0)
    iy0 = max(a.y0, b.y0)
    ix1 = min(a.x1, b.x1)
    iy1 = min(a.y1, b.y1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = (a.x1 - a.x0) * (a.y1 - a.y0)
    area_b = (b.x1 - b.x0) * (b.y1 - b.y0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0
