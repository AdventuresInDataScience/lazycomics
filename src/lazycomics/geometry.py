"""Shared panel-geometry math.

``resolution_for_aspect`` turns a panel's aspect ratio into the pixel size
``wan2gp_bridge`` requests from Wan2GP. Dimensions snap to multiples of 16
— Wan2GP floors ``resolution`` to its VAE block size (16 for Flux/Klein),
so anything finer is discarded. A coarser grid would distort narrow slots:
on a 64-grid a 0.22 slot (227×1024) rounds to 256×1024 = 0.25, 12% off.

``shape_hits_rect`` is the tail-vs-box test that ``text_renderer`` lays
bubbles out by and ``review`` checks with, so the two agree.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

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


def polygon_hits_rect(points: Sequence[Sequence[float]],
                      rect: Sequence[float]) -> bool:
    """Whether a closed polygon and an ``(x0, y0, x1, y1)`` rect share interior.

    Touching edges don't count, so a tail whose base sits on its own
    bubble's outline doesn't hit a neighbour that merely abuts it. Exact for
    convex polygons (tails are triangles); a vertex inside the rect, an edge
    crossing it, or the rect's centre inside the polygon all count.
    """
    x0, y0, x1, y1 = rect
    if x1 <= x0 or y1 <= y0 or len(points) < 2:
        return False
    if any(x0 < px < x1 and y0 < py < y1 for px, py in points):
        return True
    n = len(points)
    if any(_segment_hits_rect(points[i], points[(i + 1) % n], rect) for i in range(n)):
        return True
    return len(points) >= 3 and _point_in_polygon(((x0 + x1) / 2, (y0 + y1) / 2), points)


def _segment_hits_rect(a: Sequence[float], b: Sequence[float],
                       rect: Sequence[float]) -> bool:
    """Liang–Barsky: does segment ``a``–``b`` pass through the rect's interior?"""
    x0, y0, x1, y1 = rect
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if p == 0:
            if q <= 0:  # parallel to this edge and on or outside it
                return False
            continue
        t = q / p
        if p < 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo >= hi:
            return False
    return True


def _point_in_polygon(pt: Sequence[float], points: Sequence[Sequence[float]]) -> bool:
    """Even-odd ray cast."""
    x, y = pt
    inside = False
    n = len(points)
    for i in range(n):
        (ax, ay), (bx, by) = points[i], points[(i + 1) % n]
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            inside = not inside
    return inside


def shape_hits_rect(shape: dict[str, Any], rect: Sequence[float],
                    aim: Sequence[float] | None = None) -> bool:
    """Whether a recorded tail runs into an ``(x0, y0, x1, y1)`` rect.

    A tail is ``{"points": [...]}`` (a triangle) or ``{"dots": [[x, y, r],
    ...]}`` (a thought's trail), as ``text_renderer`` records it. With
    ``aim`` (the point it's aimed at: the speaker's face centre), the line
    of sight from its tip to there counts too: a stub that stops short of
    another bubble but points through it reads as that bubble's.
    """
    if "points" in shape:
        hit = polygon_hits_rect(shape["points"], rect)
        tip = shape["points"][-1]
    else:
        x0, y0, x1, y1 = rect
        dots = shape.get("dots") or []
        hit = any(math.hypot(x - min(max(x, x0), x1), y - min(max(y, y0), y1)) < r
                  for x, y, r in dots)
        tip = dots[-1][:2] if dots else None
    return hit or (aim is not None and tip is not None and _segment_hits_rect(tip, aim, rect))
