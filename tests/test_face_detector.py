"""Tests for lazycomics.face_detector — hybrid YuNet + MediaPipe detector.

Covers the dedup math and the merge-of-two-detectors behaviour with the
underlying detectors stubbed. The real detectors are exercised only
indirectly via the integration test (running against real panels).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics import face_detector  # noqa: E402
from lazycomics.face_detector import FaceBox, _iou, _merge_and_dedup  # noqa: E402


# ---------------------------------------------------------------------------
# IoU and dedup
# ---------------------------------------------------------------------------


def test_iou_identical_boxes_is_one():
    a = FaceBox(10, 20, 110, 220, 0.9)
    assert _iou(a, a) == 1.0


def test_iou_non_overlapping_is_zero():
    a = FaceBox(0, 0, 50, 50, 0.9)
    b = FaceBox(100, 100, 150, 150, 0.9)
    assert _iou(a, b) == 0.0


def test_iou_partial_overlap_value():
    # Two 100x100 squares overlapping by 50x50 → 2500 / (10000+10000-2500)
    a = FaceBox(0, 0, 100, 100, 0.5)
    b = FaceBox(50, 50, 150, 150, 0.5)
    expected = 2500 / 17500
    assert abs(_iou(a, b) - expected) < 1e-9


def test_dedup_drops_overlapping_keeps_highest_score():
    # Two detectors finding the same face with slight bbox drift — IoU high.
    yunet = FaceBox(100, 100, 200, 200, 0.85)
    mp = FaceBox(105, 105, 205, 205, 0.70)
    kept = _merge_and_dedup([yunet, mp])
    assert len(kept) == 1
    assert kept[0] is yunet, "dedup should keep the higher-scoring box"


def test_dedup_preserves_distinct_faces():
    # Same panel, two real faces far apart — both should survive.
    nova = FaceBox(50, 60, 150, 200, 0.8)
    rex = FaceBox(400, 60, 500, 200, 0.75)
    kept = _merge_and_dedup([nova, rex])
    assert len(kept) == 2


def test_dedup_empty_input():
    assert _merge_and_dedup([]) == []


def test_dedup_low_iou_overlap_keeps_both():
    # Slight overlap that's still below the dedup threshold — these are
    # almost-certainly two different faces touching each other (e.g.
    # heads close in a tight two-shot), not one face seen by two detectors.
    a = FaceBox(0, 0, 100, 100, 0.9)
    b = FaceBox(80, 0, 180, 100, 0.85)  # only 20% horizontal overlap
    kept = _merge_and_dedup([a, b])
    assert len(kept) == 2


# ---------------------------------------------------------------------------
# Public API plumbing — stubbed underlying detectors
# ---------------------------------------------------------------------------


def test_detect_faces_merges_both_detectors(monkeypatch):
    """When YuNet finds face A and MediaPipe finds face B, both appear."""
    monkeypatch.setattr(face_detector, "_detect_yunet",
                        lambda img: [FaceBox(10, 20, 110, 220, 0.9)])
    monkeypatch.setattr(face_detector, "_detect_mediapipe",
                        lambda img: [FaceBox(300, 400, 380, 500, 0.6)])
    faces = face_detector.detect_faces(Image.new("RGB", (640, 640)))
    assert len(faces) == 2
    scores = sorted(f.score for f in faces)
    assert scores == [0.6, 0.9]


def test_detect_faces_empty_when_both_silent(monkeypatch):
    monkeypatch.setattr(face_detector, "_detect_yunet", lambda img: [])
    monkeypatch.setattr(face_detector, "_detect_mediapipe", lambda img: [])
    assert face_detector.detect_faces(Image.new("RGB", (640, 640))) == []


def test_detect_face_centre_returns_highest_scoring(monkeypatch):
    """The centre helper should pick the most-confident face."""
    high = FaceBox(0, 0, 100, 100, 0.95)   # centre (50, 50)
    low = FaceBox(500, 500, 600, 600, 0.4)  # centre (550, 550)
    monkeypatch.setattr(face_detector, "_detect_yunet", lambda img: [high])
    monkeypatch.setattr(face_detector, "_detect_mediapipe", lambda img: [low])
    centre = face_detector.detect_face_centre(Image.new("RGB", (640, 640)))
    assert centre == (50, 50)


def test_detect_face_centre_none_when_no_faces(monkeypatch):
    monkeypatch.setattr(face_detector, "_detect_yunet", lambda img: [])
    monkeypatch.setattr(face_detector, "_detect_mediapipe", lambda img: [])
    assert face_detector.detect_face_centre(Image.new("RGB", (640, 640))) is None


# ---------------------------------------------------------------------------
# Asset wiring
# ---------------------------------------------------------------------------


def test_yunet_onnx_model_shipped_with_package():
    """The YuNet ONNX file must be present in the installed package — if
    missing, the hybrid detector silently drops back to MediaPipe-only and
    fails on the stylised-face cases the hybrid was added to fix.
    """
    assert face_detector._YUNET_MODEL_PATH.is_file(), \
        f"YuNet model missing at {face_detector._YUNET_MODEL_PATH}"
