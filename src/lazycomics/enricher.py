"""CBML → enriched per-panel JSON (plan §13.4).

Parses the project's CBML via ``cbml_parser`` and writes one JSON file per
panel under ``<project>/enriched/``. The JSON is human-readable and the
user is expected to review and edit it between ``enrich()`` and
``build_prompts()`` (workflow §6).

The split between :func:`enrich` (parses CBML) and
:func:`_enrich_from_comic` (does the real work on an already-parsed Comic)
keeps the core logic testable without ``cbml_parser`` installed.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

from lazycomics.asset_registry import get_character, get_location
from lazycomics.config import cfg_get, load_config
from lazycomics.models import (
    BubbleRegion,
    CaptionBox,
    CharacterRef,
    DialogueLine,
    PanelGenerationRequest,
    Project,
    Sfx,
)

__all__ = ["enrich"]


# Multi-character generation strategy (config: enricher.multi_char_strategy):
#   auto    — single-pass when every character on the panel is LoRA-free
#             (the inpaint path only adds drift when there's no per-character
#             LoRA to apply); multi_inpaint when any character has a LoRA so
#             each gets its own dedicated pass. Interaction keywords (hugging,
#             fighting, ...) always force single-pass since inpaint would
#             overwrite physically-overlapping characters.
#   single  — always one combined pass naming every character (no inpaint).
#   inpaint — always base pass + one inpaint pass per non-primary character.
_DEFAULT_MULTI_CHAR_STRATEGY = "auto"
_VALID_MULTI_CHAR_STRATEGIES = ("auto", "single", "inpaint")


def enrich(project: Project, *, config: dict[str, Any] | None = None) -> list[PanelGenerationRequest]:
    """Parse the project's CBML and write one enriched JSON per panel.

    Returns the list of :class:`PanelGenerationRequest` objects produced
    (in reading order). Writes
    ``<project>/enriched/page_<N>_panel_<M>.json`` per panel (1-based
    indices for human readability).

    ``config`` supplies behavioural knobs (currently
    ``enricher.multi_char_strategy``); ``None`` loads it via
    :func:`load_config`.
    """
    from cbml_parser import CBMLParser  # lazy: it's a git dep, not always present at import time

    if config is None:
        config = load_config()
    strategy_mode = cfg_get(config, "enricher.multi_char_strategy", _DEFAULT_MULTI_CHAR_STRATEGY)

    parser = CBMLParser()
    comic = parser.parse_file(str(project.cbml_path))
    return _enrich_from_comic(project, comic, strategy_mode=strategy_mode)


# ---------------------------------------------------------------------------
# Core (testable without cbml_parser installed)
# ---------------------------------------------------------------------------


def _enrich_from_comic(
    project: Project,
    comic: Any,
    *,
    strategy_mode: str = _DEFAULT_MULTI_CHAR_STRATEGY,
) -> list[PanelGenerationRequest]:
    """Build PanelGenerationRequests from an already-parsed Comic, write JSON."""
    aspect_w, aspect_h = comic.aspect  # required in v1.1; parser already resolved presets
    panels: list[PanelGenerationRequest] = []
    for page in comic.pages:
        max_col, max_row = _grid_dims(page)
        for panel_idx, parser_panel in enumerate(page.panels):
            panel = _build_panel(
                project, page, parser_panel, panel_idx,
                max_col, max_row, aspect_w, aspect_h,
                strategy_mode=strategy_mode,
            )
            panels.append(panel)
            _write_panel(project, panel)
    return panels


def _grid_dims(page: Any) -> tuple[int, int]:
    """``(max_col, max_row)`` for a page, derived from the slots themselves.

    Works for both ``PresetLayout`` and ``CustomGrid`` without inspecting
    the layout object — just takes the max endpoint across all panels.
    """
    max_col = max_row = 1
    for p in page.panels:
        max_col = max(max_col, p.slot.cols[1])
        max_row = max(max_row, p.slot.rows[1])
    return max_col, max_row


def _build_panel(
    project: Project,
    page: Any,
    parser_panel: Any,
    panel_idx: int,
    max_col: int,
    max_row: int,
    aspect_w: int,
    aspect_h: int,
    *,
    strategy_mode: str = _DEFAULT_MULTI_CHAR_STRATEGY,
) -> PanelGenerationRequest:
    page_idx = page.index  # parser uses 0-based
    panel_id = f"page_{page_idx + 1}_panel_{panel_idx + 1}"

    loc_identifier, loc_description = _resolve_loc(parser_panel.loc)
    loc_refs: list[Path] = []
    if loc_identifier:
        try:
            loc = get_location(project, loc_identifier)
        except FileNotFoundError:
            pass  # not registered; identifier preserved, default description stays
        else:
            if loc.description:
                loc_description = loc.description
            loc_refs = loc.reference_images

    chars: list[CharacterRef] = []
    for name in parser_panel.chars:
        try:
            chars.append(get_character(project, name))
        except FileNotFoundError:
            chars.append(CharacterRef(identifier=name))

    char_names = [c.identifier for c in chars]
    any_lora = any(c.lora is not None for c in chars)
    dialogue = [
        DialogueLine(character=d.character, text=d.text, bubble_type=d.bubble_type)
        for d in parser_panel.dialogue
    ]
    strategy = _compute_char_strategy(
        char_names, parser_panel.shot or "", parser_panel.action or "",
        any_lora=any_lora, mode=strategy_mode,
    )
    primary = _select_primary_character(
        char_names, parser_panel.shot or "", dialogue,
    )
    inpaint_order = _compute_inpaint_order(char_names, primary, dialogue)

    return PanelGenerationRequest(
        panel_id=panel_id,
        page_index=page_idx,
        panel_index=panel_idx,
        aspect_ratio=_compute_aspect_ratio(
            parser_panel.slot, max_col, max_row, aspect_w, aspect_h, page.span,
        ),
        bleed_edges=_compute_bleed(parser_panel.slot, max_col, max_row),
        loc_identifier=loc_identifier,
        loc_description=loc_description,
        loc_reference_images=loc_refs,
        characters=chars,
        primary_character=primary,
        char_generation_strategy=strategy,
        inpaint_order=inpaint_order,
        shot_hint=parser_panel.shot or "",
        mood=parser_panel.mood,
        action=parser_panel.action or "",
        dialogue_lines=dialogue,
        caption_boxes=[
            CaptionBox(text=c.text, bg_color=c.bg, text_color=c.color, position=c.pos)
            for c in parser_panel.captions
        ],
        sfx_lines=[
            Sfx(text=s.text, color=s.color, position=s.pos)
            for s in (getattr(parser_panel, "sfx", None) or [])
        ],
        bubble_layout=_compute_bubble_layout(char_names),
    )


def _compute_bubble_layout(char_identifiers: list[str]) -> list[BubbleRegion]:
    """Per-character planning region as fractions of the panel.

    The default geometric split — N characters get N equal horizontal slices
    in CBML authoring order (which is the parser's order, which by spec is
    not semantically ordered, but is the only signal available without an
    extra cue).

    text_renderer treats these as the *predicted* region for each speaker:
    where their dialogue bubbles should be placed and where their tail
    should point. Phase 2's inpaint_manager will use the same regions to
    pick inpaint masks, and will write back the *actual* painted regions
    on a separate field so the renderer can prefer truth over prediction.

    Single-character panels return one region covering the whole panel;
    zero-character panels return an empty list.
    """
    if not char_identifiers:
        return []
    n = len(char_identifiers)
    width = 1.0 / n
    return [
        BubbleRegion(
            character=name,
            x_frac=i * width,
            y_frac=0.0,
            width_frac=width,
            height_frac=1.0,
        )
        for i, name in enumerate(char_identifiers)
    ]


_INTERACTION_KEYWORDS = frozenset((
    "fighting", "fight", "grappling", "grapple", "wrestling", "wrestle",
    "embracing", "embrace", "hugging", "hug", "kissing", "kiss",
    "carrying", "carry", "holding hands", "hand in hand",
    "tackling", "tackle", "pinning", "choking", "strangling",
    "dancing together", "dancing with", "leaning on", "leaning against",
    "back to back", "back-to-back", "shoulder to shoulder",
    "arm in arm", "arm-in-arm", "piggyback", "intertwined",
    "tangled", "clashing", "collision", "colliding",
    "pulling", "pushing", "dragging", "grabbing", "grabbed",
    "overlapping", "on top of", "beneath", "under",
))


def _compute_char_strategy(
    char_names: list[str],
    shot_hint: str = "",
    action: str = "",
    *,
    any_lora: bool = False,
    mode: str = _DEFAULT_MULTI_CHAR_STRATEGY,
) -> str:
    """Decide ``"single"`` vs ``"multi_inpaint"`` for a panel.

    A 0–1 character panel is always ``"single"`` — there's nothing to inpaint.

    For 2+ characters, ``mode`` (``enricher.multi_char_strategy``) decides:

    * ``"single"`` — always one combined pass naming every character.
    * ``"inpaint"`` — always base pass + one inpaint pass per non-primary
      character.
    * ``"auto"`` (default) — single-pass unless inpaint actually buys
      something:
        - interaction keywords (hugging, fighting, ...) → ``"single"``,
          because inpaint would overwrite physically-overlapping characters;
        - no character has a LoRA → ``"single"``, because with no
          per-character adapter the inpaint pass does nothing the combined
          prompt can't, and only adds drift;
        - otherwise → ``"multi_inpaint"`` so each LoRA character gets its
          own dedicated pass.

    The wan2gp_bridge reads the resulting ``char_generation_strategy`` to
    route the panel (single batch path vs sequential inpaint).
    """
    if mode not in _VALID_MULTI_CHAR_STRATEGIES:
        raise ValueError(
            f"enricher.multi_char_strategy must be one of "
            f"{_VALID_MULTI_CHAR_STRATEGIES}, got {mode!r}"
        )

    if len(char_names) < 2:
        return "single"
    if mode == "single":
        return "single"
    if mode == "inpaint":
        return "multi_inpaint"

    # auto
    combined = f"{shot_hint} {action}".lower()
    for kw in _INTERACTION_KEYWORDS:
        if kw in combined:
            return "single"
    if not any_lora:
        return "single"
    return "multi_inpaint"


def _select_primary_character(
    char_names: list[str],
    shot_hint: str,
    dialogue: list[DialogueLine],
) -> str | None:
    """Pick the character generated first (before any inpaint passes).

    Resolution order (plan §7.4):
    1. Regex match — if any character identifier appears as a word boundary
       in the ``shot`` string (case-insensitive), the first match wins. This
       covers explicit CBML cues like ``shot: closeup on NOVA``.
    2. Dialogue count — the character with the most dialogue lines is the
       focal speaker for the panel.
    3. First listed — fall back to CBML ``chars:`` order.

    Returns ``None`` for panels with no characters.
    """
    if not char_names:
        return None
    if len(char_names) == 1:
        return char_names[0]

    # 1. Shot-hint regex match
    if shot_hint:
        shot_lower = shot_hint.lower()
        for name in char_names:
            if re.search(rf"\b{re.escape(name.lower())}\b", shot_lower):
                return name

    # 2. Most dialogue lines
    counts: dict[str, int] = {n: 0 for n in char_names}
    for dl in dialogue:
        if dl.character in counts:
            counts[dl.character] += 1
    max_count = max(counts.values())
    if max_count > 0:
        for name in char_names:
            if counts[name] == max_count:
                return name

    # 3. First listed
    return char_names[0]


def _compute_inpaint_order(
    char_names: list[str],
    primary: str | None,
    dialogue: list[DialogueLine],
) -> list[str]:
    """Character generation order: primary first, rest by dialogue count desc.

    For single-character or zero-character panels this returns the
    trivial list (``[primary]`` or ``[]``). For multi-character panels the
    primary is generated in the base pass and the remaining characters are
    inpainted in dialogue-count-descending order (most important first, so
    their masks get the cleanest canvas).
    """
    if not char_names:
        return []
    if len(char_names) == 1:
        return list(char_names)

    rest = [n for n in char_names if n != primary]

    # Sort rest by dialogue count descending, stable on original order
    counts: dict[str, int] = {n: 0 for n in rest}
    for dl in dialogue:
        if dl.character in counts:
            counts[dl.character] += 1
    rest.sort(key=lambda n: counts[n], reverse=True)

    return [primary] + rest if primary else rest


def _compute_aspect_ratio(
    slot: Any, max_col: int, max_row: int,
    aspect_w: int, aspect_h: int, span: int,
) -> float:
    """Panel aspect derived from canvas aspect, grid dims, and slot range.

    For a spread (``span > 1``) the canvas is ``aspect_w × span`` wide by
    ``aspect_h`` tall — per the standard, spread aspect is computed from
    the base page aspect and the span count.
    """
    canvas_w = aspect_w * span
    canvas_h = aspect_h
    slot_cols = slot.cols[1] - slot.cols[0] + 1
    slot_rows = slot.rows[1] - slot.rows[0] + 1
    panel_w_units = canvas_w * slot_cols / max_col
    panel_h_units = canvas_h * slot_rows / max_row
    return panel_w_units / panel_h_units


def _resolve_loc(loc_text: str) -> tuple[str | None, str]:
    """Apply the §12 rule: no spaces → identifier, otherwise → free-text.

    For an identifier, the default description is the identifier with
    underscores swapped for spaces. If the location is registered, the
    caller overrides this with the registered description.
    """
    loc_text = loc_text.strip()
    if " " in loc_text:
        return None, loc_text
    return loc_text, loc_text.replace("_", " ")


def _compute_bleed(slot: Any, max_col: int, max_row: int) -> set[str]:
    """Edges the panel's slot touches. Subset of ``{top, right, bottom, left}``."""
    edges: set[str] = set()
    if slot.cols[0] <= 1:
        edges.add("left")
    if slot.cols[1] >= max_col:
        edges.add("right")
    if slot.rows[0] <= 1:
        edges.add("top")
    if slot.rows[1] >= max_row:
        edges.add("bottom")
    return edges


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def _write_panel(project: Project, panel: PanelGenerationRequest) -> None:
    out_path = project.enriched_dir / f"{panel.panel_id}.json"
    data = _to_json_primitives(dataclasses.asdict(panel))
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _to_json_primitives(obj: Any) -> Any:
    """Recursively convert a Python value to JSON-native primitives.

    Output is composed only of ``dict``, ``list``, ``str``, ``int``,
    ``float``, ``bool``, ``None`` — safe to pass to :func:`json.dump`.

    ``dataclasses.asdict`` recurses through dataclasses, dicts, lists, and
    tuples but leaves :class:`pathlib.Path` and :class:`set` as-is. This
    function fills that gap: ``Path`` becomes its string form, ``set``
    becomes a sorted ``list`` (for deterministic on-disk output), and
    ``tuple`` becomes ``list`` (JSON has no tuple type).
    """
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, set):
        try:
            return sorted(obj)
        except TypeError:
            return list(obj)
    if isinstance(obj, tuple):
        return [_to_json_primitives(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_json_primitives(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json_primitives(v) for v in obj]
    return obj
