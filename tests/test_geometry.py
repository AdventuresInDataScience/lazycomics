"""Tests for lazycomics.geometry — the shared aspect→resolution helper."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics.geometry import resolution_for_aspect  # noqa: E402


def test_square_is_unchanged():
    assert resolution_for_aspect(1.0, 1024) == (1024, 1024)


def test_portrait_long_edge_is_height():
    w, h = resolution_for_aspect(2 / 3, 1024)
    assert h == 1024
    assert w < h
    assert w % 16 == 0


def test_landscape_long_edge_is_width():
    w, h = resolution_for_aspect(3 / 2, 1024)
    assert w == 1024
    assert h < w
    assert h % 16 == 0


def test_short_edge_snapped_to_multiple_of_16():
    for aspect in (2 / 3, 3 / 2, 1 / 3, 16 / 9, 0.8, 1.25):
        w, h = resolution_for_aspect(aspect, 1024)
        assert w % 16 == 0 and h % 16 == 0, f"{aspect} -> {(w, h)} not on 16-grid"


def test_extreme_aspect_floored_at_64():
    # A wildly thin panel must not collapse the short edge to nothing.
    w, h = resolution_for_aspect(0.01, 1024)
    assert w >= 64 and h == 1024
    w, h = resolution_for_aspect(100.0, 1024)
    assert h >= 64 and w == 1024


def test_degenerate_aspect_treated_as_square():
    assert resolution_for_aspect(0.0, 1024) == (1024, 1024)
    assert resolution_for_aspect(-2.0, 1024) == (1024, 1024)


def test_base_resolution_respected():
    w, h = resolution_for_aspect(2 / 3, 512)
    assert h == 512
    assert max(w, h) == 512


def test_aspect_preserved_within_snap_tolerance():
    # The returned aspect should be close to the requested one (off only by
    # the 16-grid snap of the short edge) — including the narrow slots in
    # the e2e story (2/9, 4/9), which a 64-grid distorted by up to 12%.
    for aspect in (2 / 9, 4 / 9, 2 / 3, 3 / 2, 4 / 5, 5 / 4, 2.0):
        w, h = resolution_for_aspect(aspect, 1024)
        assert abs((w / h) - aspect) / aspect < 0.05, f"{aspect} -> {(w, h)}"
