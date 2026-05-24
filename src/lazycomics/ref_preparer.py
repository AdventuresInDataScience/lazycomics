"""Reference image preparation (plan §13.7, §9).

Builds the per-panel reference image stack that gets passed to the
generator (Wan2GP / Flux Klein 9B). Each panel ends up with one
**primary** ref, cropped to the panel's target aspect ratio with a
face-aware crop (via MediaPipe), plus zero or more **supporting** refs
(style, location, additional characters) that pass through at their
native aspect ratio.

The primary ref is selected from the ``primary_character``'s registered
images when the enricher has set that field — this ensures the ref
matches the character generated in the base Flux pass. Supporting refs
follow ``inpaint_order`` so the bridge receives them in generation
sequence. Both fall back to the original Phase 1 heuristics (first
char with a ref, original chars: order) when processing older enriched
JSON that lacks these fields.

File layout (plan §11):

    refs_prepared/
    ├── page_1_panel_1_ref.png       # primary (aspect-matched)
    ├── page_1_panel_1_ref_1.png     # supporting ref 1
    └── page_1_panel_1_ref_2.png     # supporting ref 2

The user can override any prepared ref by dropping a PNG in
``refs_prepared/`` with the matching filename. ``prepare_references()``
skips panels whose primary ref already exists unless ``force=True``.

MediaPipe is a hard dependency — face-aware cropping is required to
honour the user's authoring intent (e.g. a CBML ``shot: extreme closeup
on face`` must not silently fall back to a centre crop that lands on
the body). The only catch-all guard is around a per-image runtime
failure (corrupt JPEG, etc.) so one bad source doesn't tank a whole
project run — those are logged loudly and that single panel uses
centre crop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from lazycomics import face_detector
from lazycomics.asset_registry import get_style
from lazycomics.models import Project, StyleAsset

__all__ = ["prepare_references"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def prepare_references(
    project: Project,
    panels: list[str] | None = None,
    *,
    force: bool = False,
    working_resolution: int = 1024,
) -> dict[str, list[Path]]:
    """Prepare reference images for each enriched panel.

    Reads ``<project>/enriched/*.json`` and writes
    ``<project>/refs_prepared/<panel_id>_ref.png`` (primary) plus
    ``_ref_1.png``, ``_ref_2.png`` ... (supporting). Returns
    ``{panel_id: [primary_path, *supporting_paths]}``.

    Parameters
    ----------
    project
        The lazycomics project.
    panels
        Panel IDs to process; ``None`` processes every enriched panel.
    force
        If ``False`` (default), panels whose primary ref already exists
        are skipped (the user may have hand-overridden it). If ``True``,
        all referenced panels are re-prepared.
    working_resolution
        Long-edge target in pixels for the prepared images. Defaults to
        1024 — a sweet spot for Klein 9B per plan §9. Larger values cost
        VRAM at generation time.
    """
    style = _try_get_style(project)
    style_refs = list(style.reference_images) if style else []

    if panels is None:
        panel_ids = sorted(p.stem for p in project.enriched_dir.glob("*.json"))
    else:
        panel_ids = list(panels)

    results: dict[str, list[Path]] = {}
    for panel_id in panel_ids:
        enriched_path = project.enriched_dir / f"{panel_id}.json"
        if not enriched_path.is_file():
            print(f"[refs] skipping {panel_id}: no enriched JSON")
            continue

        primary_out = project.refs_prepared_dir / f"{panel_id}_ref.png"
        if primary_out.is_file() and not force:
            # User-overridden or already prepared — report existing files.
            existing = sorted(project.refs_prepared_dir.glob(f"{panel_id}_ref*.png"))
            results[panel_id] = existing
            continue

        panel = json.loads(enriched_path.read_text(encoding="utf-8"))
        outputs = _prepare_one(project, panel, style_refs, working_resolution)
        if outputs:
            results[panel_id] = outputs

    return results


# ---------------------------------------------------------------------------
# Per-panel preparation
# ---------------------------------------------------------------------------


def _prepare_one(
    project: Project,
    panel: dict[str, Any],
    style_refs: list[Path],
    working_resolution: int,
) -> list[Path]:
    panel_id = panel["panel_id"]
    target_aspect = float(panel.get("aspect_ratio") or 1.0)

    primary_source = _select_primary_source(panel)
    if primary_source is None:
        print(f"[refs] {panel_id}: no source images available, skipping")
        return []

    if not primary_source.is_file():
        print(f"[refs] {panel_id}: primary source missing: {primary_source}")
        return []

    # Primary: crop to target aspect (face-aware), resize to working res.
    img = Image.open(primary_source)
    img = _crop_to_aspect(img, target_aspect)
    img = _resize_long_edge(img, working_resolution)

    primary_out = project.refs_prepared_dir / f"{panel_id}_ref.png"
    img.save(primary_out)
    outputs = [primary_out]

    # Supporting: passthrough at native aspect, only resize.
    # Style refs go FIRST in the supporting list so they end up at
    # image_refs[0] in the Wan2GP task — earlier positions get stronger
    # conditioning weight than later ones in Klein/Kontext.
    supporting_sources = [Path(p) for p in style_refs] + _collect_supporting(panel, primary_source)
    for idx, src in enumerate(supporting_sources, start=1):
        if not src.is_file():
            continue
        s_img = Image.open(src)
        s_img = _resize_long_edge(s_img, working_resolution)
        out_path = project.refs_prepared_dir / f"{panel_id}_ref_{idx}.png"
        s_img.save(out_path)
        outputs.append(out_path)

    return outputs


# ---------------------------------------------------------------------------
# Selection heuristics (plan §9.2)
# ---------------------------------------------------------------------------


_WIDE_SHOT_KEYWORDS = ("wide", "establishing", "establish", "long shot", "bird")


def _select_primary_source(panel: dict[str, Any]) -> Path | None:
    """Choose the source image that becomes the primary reference.

    Resolution order:
    1. Wide / establishing shots → location ref (background dominates).
    2. No characters → location ref.
    3. ``primary_character`` field (set by the enricher) → that character's
       first ref image. This aligns the primary reference with the character
       generated in the base Flux pass.
    4. First character that has a ref image (fallback for older enriched
       JSON or when the primary has no registered refs).
    5. Location ref, then ``None``.
    """
    shot = (panel.get("shot_hint") or "").lower()
    chars = panel.get("characters") or []
    loc_refs = [Path(p) for p in (panel.get("loc_reference_images") or [])]

    is_wide = any(kw in shot for kw in _WIDE_SHOT_KEYWORDS)

    # Wide / establishing: prefer location.
    if is_wide and loc_refs:
        return loc_refs[0]

    # No characters in scene: use location.
    if not chars and loc_refs:
        return loc_refs[0]

    # Prefer the primary character's ref when the enricher set one.
    primary_name = panel.get("primary_character")
    if primary_name:
        for char in chars:
            if char.get("identifier") == primary_name:
                char_refs = char.get("reference_images") or []
                if char_refs:
                    return Path(char_refs[0])
                break  # primary found but has no refs — fall through

    # Fallback: first character that has a ref image.
    for char in chars:
        char_refs = char.get("reference_images") or []
        if char_refs:
            return Path(char_refs[0])

    # Last resort: location ref if available, else nothing.
    return loc_refs[0] if loc_refs else None


def _collect_supporting(panel: dict[str, Any], primary: Path) -> list[Path]:
    """Non-primary refs for the panel: location refs + character refs.

    Character refs are ordered by ``inpaint_order`` when available (so the
    bridge receives them in generation sequence). Falls back to the
    original ``characters`` list order for older enriched JSON.
    """
    refs: list[Path] = []

    # Location refs first — background context.
    for p in panel.get("loc_reference_images") or []:
        path = Path(p)
        if path != primary and path not in refs:
            refs.append(path)

    # Build a lookup: identifier → first ref image path.
    chars = panel.get("characters") or []
    char_first_ref: dict[str, Path] = {}
    for char in chars:
        char_refs = char.get("reference_images") or []
        if char_refs:
            char_first_ref[char["identifier"]] = Path(char_refs[0])

    # Order by inpaint_order if available; otherwise chars list order.
    inpaint_order = panel.get("inpaint_order") or [c["identifier"] for c in chars]
    for name in inpaint_order:
        path = char_first_ref.get(name)
        if path and path != primary and path not in refs:
            refs.append(path)

    return refs


# ---------------------------------------------------------------------------
# Image ops
# ---------------------------------------------------------------------------


def _crop_to_aspect(img: Image.Image, target_aspect: float) -> Image.Image:
    """Crop ``img`` to ``target_aspect`` (w/h), face-aware when possible.

    If source already matches target to within ~0.3% no crop is applied
    (avoids one-pixel resizing artefacts). When MediaPipe finds a face,
    the crop window centres on the face; otherwise it centres on the
    image.
    """
    w, h = img.size
    if h <= 0 or target_aspect <= 0:
        return img
    src_aspect = w / h
    if abs(src_aspect - target_aspect) < 0.003:
        return img

    face_centre = _detect_face_centre(img)

    if src_aspect > target_aspect:
        # Source too wide — crop horizontally.
        new_w = max(1, int(round(h * target_aspect)))
        if face_centre:
            cx = face_centre[0]
        else:
            cx = w // 2
        x1 = max(0, min(w - new_w, cx - new_w // 2))
        return img.crop((x1, 0, x1 + new_w, h))

    # Source too tall — crop vertically.
    new_h = max(1, int(round(w / target_aspect)))
    if face_centre:
        cy = face_centre[1]
    else:
        cy = h // 2
    y1 = max(0, min(h - new_h, cy - new_h // 2))
    return img.crop((0, y1, w, y1 + new_h))


def _resize_long_edge(img: Image.Image, target: int) -> Image.Image:
    """Downscale so the longer edge equals ``target``. No-op if already smaller."""
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= target:
        return img
    scale = target / long_edge
    new_size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    return img.resize(new_size, Image.LANCZOS)


def _detect_face_centre(img: Image.Image) -> tuple[int, int] | None:
    """Return ``(x, y)`` of the most-confident detected face, or ``None``.

    Delegates to the hybrid YuNet + MediaPipe detector. Returns ``None``
    when no face is detected — that's the correct behaviour for non-face
    images (locations, objects) and the caller then centre-crops.
    Module-level so tests can monkeypatch.
    """
    return face_detector.detect_face_centre(img)


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def _try_get_style(project: Project) -> StyleAsset | None:
    try:
        return get_style(project)
    except FileNotFoundError:
        return None
