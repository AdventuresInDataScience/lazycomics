"""Shared panel-geometry math.

``resolution_for_aspect`` turns a panel's aspect ratio into the pixel size
``wan2gp_bridge`` requests from Wan2GP. Dimensions snap to multiples of 16
— Wan2GP floors ``resolution`` to its VAE block size (16 for Flux/Klein),
so anything finer is discarded. A coarser grid would distort narrow slots:
on a 64-grid a 0.22 slot (227×1024) rounds to 256×1024 = 0.25, 12% off.
"""

from __future__ import annotations

_GRID = 16
_MIN_EDGE = 64


def _snap(value: float) -> int:
    """Round ``value`` to the nearest multiple of the grid (>= ``_MIN_EDGE``)."""
    v = int(round(value))
    return max(_MIN_EDGE, ((v + _GRID // 2) // _GRID) * _GRID)


def resolution_for_aspect(aspect_ratio: float, base_resolution: int) -> tuple[int, int]:
    """Return ``(width, height)`` in pixels for a panel of the given aspect.

    ``aspect_ratio`` is width/height (so < 1 is portrait, > 1 landscape).
    The long edge is ``base_resolution``; the short edge is derived from the
    aspect and snapped to a multiple of 16 (Wan2GP's block size), floored at
    64px so extreme aspects can't collapse.
    """
    if aspect_ratio <= 0:
        aspect_ratio = 1.0
    if aspect_ratio >= 1.0:
        # Landscape or square: width is the long edge.
        return base_resolution, _snap(base_resolution / aspect_ratio)
    # Portrait: height is the long edge.
    return _snap(base_resolution * aspect_ratio), base_resolution
