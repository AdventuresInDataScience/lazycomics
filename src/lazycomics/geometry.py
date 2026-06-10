"""Shared panel-geometry math.

Lives in its own module so the two stages that must agree on a panel's
pixel dimensions — ``ref_preparer`` (which shapes the primary reference)
and ``wan2gp_bridge`` (which asks Wan2GP for an output resolution) — derive
those dimensions from one place and can never drift apart.

Why this matters: Klein/Kontext takes its *output* aspect from the
``image_start`` reference image, overriding the ``resolution`` field of the
task. Wan2GP buckets that reference to the model's latent grid (multiples
of 64); a reference whose dimensions are not already on that grid gets
snapped to a canonical near-square size, so the panel comes out the wrong
shape. The fix is to prepare ``image_start`` at *exactly* the resolution
the bridge will request — which means both stages must compute that
resolution identically. Hence this single helper.
"""

from __future__ import annotations

_LATENT_MULTIPLE = 64


def _snap(value: float) -> int:
    """Round ``value`` to the nearest multiple of the latent grid (>= one cell)."""
    v = int(round(value))
    return max(_LATENT_MULTIPLE, ((v + _LATENT_MULTIPLE // 2) // _LATENT_MULTIPLE) * _LATENT_MULTIPLE)


def resolution_for_aspect(aspect_ratio: float, base_resolution: int) -> tuple[int, int]:
    """Return ``(width, height)`` in pixels for a panel of the given aspect.

    ``aspect_ratio`` is width/height (so < 1 is portrait, > 1 landscape).
    The long edge is ``base_resolution``; the short edge is derived from the
    aspect and snapped to a multiple of 64 (the diffusion latent grid). The
    long edge stays exactly ``base_resolution`` (already a multiple of 64 in
    practice), so the returned size sits cleanly on the latent grid.
    """
    if aspect_ratio <= 0:
        aspect_ratio = 1.0
    if aspect_ratio >= 1.0:
        # Landscape or square: width is the long edge.
        return base_resolution, _snap(base_resolution / aspect_ratio)
    # Portrait: height is the long edge.
    return _snap(base_resolution * aspect_ratio), base_resolution
