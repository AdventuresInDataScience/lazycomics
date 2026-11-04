"""Reference image preparation (plan §13.7, §9).

Builds the per-panel reference image stack that gets passed to the
generator (Wan2GP / Flux Klein 9B). Each panel ends up with one
**primary** ref plus zero or more **supporting** refs (style, location,
additional characters). The bridge sends them all, primary first, as
Klein ``image_refs``.

Every ref keeps its native aspect ratio and is only downscaled to
``working_resolution`` on the long edge. Wan2GP rescales each ``image_refs``
entry to the output's pixel area at the ref's own aspect, so nothing here
needs to match the panel shape — and cropping a character reference to a
narrow panel slot would just throw away most of the character.

The primary ref is selected from the ``primary_character``'s registered
images when the enricher has set that field — this ensures the ref
matches the character generated in the base Flux pass. Supporting refs
follow ``inpaint_order`` so the bridge receives them in generation
sequence. Both fall back to the original Phase 1 heuristics (first
char with a ref, original chars: order) when processing older enriched
JSON that lacks these fields. Klein has no per-ref weight, so the order
and count are tunable (``refs.*`` config, see :func:`_order_sources`).

File layout (plan §11):

    refs_prepared/
    ├── page_1_panel_1_ref.png       # primary (aspect-matched)
    ├── page_1_panel_1_ref_1.png     # supporting ref 1
    └── page_1_panel_1_ref_2.png     # supporting ref 2

The user can override any prepared ref by dropping a PNG in
``refs_prepared/`` with the matching filename. ``prepare_references()``
skips panels whose primary ref already exists unless ``force=True``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from lazycomics.asset_registry import get_style
from lazycomics.config import cfg_get, load_config
from lazycomics.models import Project, StyleAsset

__all__ = ["prepare_references"]


# Ref ordering knobs (``refs.*`` in lazycomics_config.yaml). Module constants
# so the shipped config and the parity test stay in lockstep. Characters
# first and a cap of 4 (W10.3): generation 11% faster on the subset with the
# consistency bar holding; the cap then drops style and location refs before
# any character's.
_DEFAULT_CHARACTERS_FIRST = True
_DEFAULT_CLOSEUP_LOCATION = False
_DEFAULT_MAX_REFS = 4


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def prepare_references(
    project: Project,
    panels: list[str] | None = None,
    *,
    force: bool = False,
    working_resolution: int = 1024,
    max_refs: int | None = None,
    config: dict[str, Any] | None = None,
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
        Long-edge cap in pixels for the prepared images (downscale only).
        Defaults to 1024. Wan2GP rescales every ref to the output's pixel
        area anyway, so this sets source quality, not generation cost.
    max_refs
        Most refs sent per panel, cut from the end of the stack; overrides
        ``refs.max_refs``. ``0`` = no cap; ``None`` uses the config.
    config
        Loaded config dict for the ``refs.*`` ordering knobs; if ``None``,
        calls ``load_config()`` internally.
    """
    if config is None:
        config = load_config()
    opts = {
        "characters_first": cfg_get(config, "refs.characters_first", _DEFAULT_CHARACTERS_FIRST),
        "closeup_location": cfg_get(config, "refs.closeup_location", _DEFAULT_CLOSEUP_LOCATION),
        "max_refs": max_refs if max_refs is not None
                    else cfg_get(config, "refs.max_refs", _DEFAULT_MAX_REFS),
    }
    style = _try_get_style(project)
    style_refs = [Path(p) for p in style.reference_images] if style else []

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
        outputs = _prepare_one(project, panel, style_refs, working_resolution, opts)
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
    opts: dict[str, Any],
) -> list[Path]:
    panel_id = panel["panel_id"]

    sources = _order_sources(panel, style_refs, opts)
    if not sources:
        print(f"[refs] {panel_id}: no source images available, skipping")
        return []

    if not sources[0].is_file():
        print(f"[refs] {panel_id}: primary source missing: {sources[0]}")
        return []

    sources = [s for s in sources if s.is_file()]
    if opts["max_refs"]:
        sources = sources[: opts["max_refs"]]

    # This replaces the whole stack. A leftover _ref_N from a longer one would
    # still be sent by the bridge, which collects _ref_1, _ref_2 ... until a gap.
    for stale in project.refs_prepared_dir.glob(f"{panel_id}_ref_*.png"):
        stale.unlink()

    # Every ref: whole image at native aspect, numbered with no gaps.
    outputs: list[Path] = []
    for src in sources:
        suffix = f"_{len(outputs)}" if outputs else ""
        out_path = project.refs_prepared_dir / f"{panel_id}_ref{suffix}.png"
        _resize_long_edge(Image.open(src), working_resolution).save(out_path)
        outputs.append(out_path)

    return outputs


# ---------------------------------------------------------------------------
# Selection heuristics (plan §9.2)
# ---------------------------------------------------------------------------


_WIDE_SHOT_KEYWORDS = ("wide", "establishing", "establish", "long shot", "bird")


def _order_sources(
    panel: dict[str, Any],
    style_refs: list[Path],
    opts: dict[str, Any],
) -> list[Path]:
    """Every source for the panel, deduplicated, in the order the bridge sends them.

    Klein has no per-ref weight, so order and count are the only levers
    (``Docs/PLAN.md`` W10.3). ``characters_first`` (the default) puts every
    character (primary first) before style and location, wide shots
    included; without it the order is primary (see
    :func:`_select_primary_source`), style, location, other characters.
    ``closeup_location: false`` (the default) drops the location ref on
    close-ups that show a character.
    """
    primary = _select_primary_source(panel)
    if primary is None:
        return []
    chars = _character_sources(panel)
    if opts["characters_first"] and chars:
        order = chars + style_refs + [Path(p) for p in panel.get("loc_reference_images") or []]
    else:
        order = [primary] + style_refs + _collect_supporting(panel, primary)
    if not opts["closeup_location"] and chars and _is_closeup(panel):
        locs = {Path(p) for p in panel.get("loc_reference_images") or []}
        order = [p for p in order if p not in locs]
    return list(dict.fromkeys(order))


def _character_sources(panel: dict[str, Any]) -> list[Path]:
    """Each character's first ref: ``primary_character``, then ``inpaint_order``."""
    chars = panel.get("characters") or []
    first_ref = {c["identifier"]: Path(c["reference_images"][0])
                 for c in chars if c.get("reference_images")}
    names = [c["identifier"] for c in chars]
    order = [panel.get("primary_character"), *(panel.get("inpaint_order") or []), *names]
    return list(dict.fromkeys(first_ref[n] for n in order if n in first_ref))


def _is_closeup(panel: dict[str, Any]) -> bool:
    """Shot hint says close-up ("closeup", "close-up", "extreme close up" ...)."""
    return "close" in (panel.get("shot_hint") or "").lower()


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


def _resize_long_edge(img: Image.Image, target: int) -> Image.Image:
    """Downscale so the longer edge equals ``target``. No-op if already smaller."""
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= target:
        return img
    scale = target / long_edge
    new_size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    return img.resize(new_size, Image.LANCZOS)


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def _try_get_style(project: Project) -> StyleAsset | None:
    try:
        return get_style(project)
    except FileNotFoundError:
        return None
