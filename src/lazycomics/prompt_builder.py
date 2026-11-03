"""enriched JSON → prompt text files (plan §13.5).

Reads each ``<project>/enriched/*.json`` and writes a paired
``<project>/prompts/<panel_id>.txt`` (positive prompt) and
``<panel_id>.neg.txt`` (negative prompt). Returns
``{panel_id: (prompt, neg_prompt)}``.

The prompt is a comma-separated string composed in a fixed order:

    style_prefix, style_trigger, group_cue, char_triggers_or_descriptions,
    action, shot_hint, mood, setting: loc_description

``group_cue`` only appears on panels with three or more characters, two or
more of whom have lines ("four people, left to right: Ana, Ben, Cal and Dee,
each clearly visible"), in the enricher's ``bubble_layout`` order: see
:func:`group_cue`.

This is a starting template tuned for Flux-family models (natural prose
with comma separators, LoRA triggers up front). The workflow expects the
user to review and edit the ``.txt`` files between this step and image
generation (plan §6), so the template is a sane default, not a final word.

For each character: its LoRA trigger word if it has one (the LoRA encodes
appearance), then its visual description labelled with its name
(``Nova (blue hair)``); with neither, the bare identifier.

``build_prompts`` warns when a setting or shot mentions signs, posters,
banners or billboards: the image model fills them with gibberish lettering.

Empty / missing fields are silently skipped. The negative prompt is the
same default for every panel; users edit per-panel as needed.
"""

from __future__ import annotations

import json
import re
from typing import Any

from lazycomics.asset_registry import get_style
from lazycomics.models import Project, StyleAsset

__all__ = ["build_prompts", "DEFAULT_NEGATIVE_PROMPT"]


DEFAULT_NEGATIVE_PROMPT = (
    "speech bubble, speech balloon, word balloon, dialogue bubble, "
    "thought bubble, caption box, caption, text, lettering, "
    "sound effect text, comic panel border, gutter, "
    "blurry, low quality, distorted hands, extra fingers, "
    "watermark, signature, jpeg artifacts"
)

# Panels with this many characters or more, of whom this many have lines, get
# a cue naming them all, left to right (Docs/PLAN.md problem 16). The order is
# the enricher's bubble_layout order, the left-to-right prior the text renderer
# matches speakers to faces with, so the cue is only added where tails have to
# tell speakers apart: nothing else consumes the order.
_GROUP_CUE_MIN = 3
_GROUP_CUE_SPEAKERS = 2
_COUNT_WORDS = {3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}

# Scenery the image model fills with gibberish lettering (Docs/PLAN.md
# problem 7). Neither the negative prompt nor the LLM refiner removes it;
# rewording the location description does. The refiner's checks also reject
# a refined prompt that adds one.
SIGNAGE = re.compile(r"\b(signs?|signage|posters?|banners?|billboards?)\b", re.IGNORECASE)


def build_prompts(project: Project) -> dict[str, tuple[str, str]]:
    """Build prompt + negative-prompt files for every enriched panel.

    Returns ``{panel_id: (prompt, negative_prompt)}``. Side-effect: writes
    ``<project>/prompts/<panel_id>.txt`` and ``<panel_id>.neg.txt`` per
    panel, in sorted panel-id order.
    """
    style = _try_get_style(project)

    results: dict[str, tuple[str, str]] = {}
    warned: set[tuple[str, str]] = set()
    for path in sorted(project.enriched_dir.glob("*.json")):
        panel = json.loads(path.read_text(encoding="utf-8"))
        prompt, neg = _build_prompt(panel, style)

        # Once per location for its description, once per panel for its shot.
        for field, text, key in (
            ("setting", panel.get("loc_description"), panel.get("loc_identifier") or panel["panel_id"]),
            ("shot", panel.get("shot_hint"), panel["panel_id"]),
        ):
            match = SIGNAGE.search(text or "")
            if match and (field, key) not in warned:
                warned.add((field, key))
                print(f"[prompts] {key}: the {field} mentions {match.group(0)!r}, which the image "
                      "model letters with gibberish. Describe them by shape and light instead "
                      "(e.g. 'abstract neon light shapes').")

        panel_id = panel["panel_id"]
        (project.prompts_dir / f"{panel_id}.txt").write_text(prompt + "\n", encoding="utf-8")
        (project.prompts_dir / f"{panel_id}.neg.txt").write_text(neg + "\n", encoding="utf-8")
        results[panel_id] = (prompt, neg)
    return results


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _try_get_style(project: Project) -> StyleAsset | None:
    try:
        return get_style(project)
    except FileNotFoundError:
        return None


def _build_prompt(panel: dict[str, Any], style: StyleAsset | None) -> tuple[str, str]:
    """Compose prompt + neg-prompt from a panel dict and the project style."""
    parts: list[str] = []

    if style and style.prompt_prefix:
        parts.append(style.prompt_prefix.strip())

    if style and style.lora and style.lora.trigger_word:
        parts.append(style.lora.trigger_word.strip())

    parts.append(group_cue(panel))

    for char in panel.get("characters", []):
        text = _char_to_prompt_text(char)
        if text:
            parts.append(text)

    if panel.get("action"):
        parts.append(panel["action"].strip())

    if panel.get("shot_hint"):
        parts.append(panel["shot_hint"].strip())

    if panel.get("mood"):
        parts.append(panel["mood"].strip())

    loc = (panel.get("loc_description") or "").strip()
    if loc:
        parts.append(f"setting: {loc}")

    prompt = ", ".join(p for p in parts if p)
    return prompt, DEFAULT_NEGATIVE_PROMPT


def group_cue(panel: dict[str, Any]) -> str:
    """``"four people, left to right: A, B, C and D, each clearly visible"``, or ``""``.

    ``panel`` is the enriched JSON. The cue needs ``_GROUP_CUE_MIN`` or more
    characters, ``_GROUP_CUE_SPEAKERS`` or more of whom have a line of any
    bubble type. ``llm_refiner`` checks that a refined prompt keeps it.
    """
    chars = panel.get("characters", [])
    names = [_display_name(c) for c in chars if _display_name(c)]
    cast = {c.get("identifier") for c in chars}
    speakers = {d.get("character") for d in panel.get("dialogue_lines", []) if d.get("text")} & cast
    if len(names) < _GROUP_CUE_MIN or len(speakers) < _GROUP_CUE_SPEAKERS:
        return ""
    count = _COUNT_WORDS.get(len(names), str(len(names)))
    return f"{count} people, left to right: {', '.join(names[:-1])} and {names[-1]}, each clearly visible"


def _display_name(char: dict[str, Any]) -> str:
    """``OLD_MAN`` -> ``Old Man``: how the action text names a character."""
    return (char.get("identifier") or "").strip().replace("_", " ").title()


def _char_to_prompt_text(char: dict[str, Any]) -> str:
    """Trigger word and/or visual description for one character.

    - LoRA present and description set → both, comma-joined (LoRA pins
      identity, description carries clothing/state/etc. that the LoRA
      typically doesn't reliably capture).
    - LoRA only → trigger word.
    - Description only → description.
    - Neither → bare identifier.

    A description is labelled with the character's name, ``Nova (blue
    hair)``, so the action and the group cue, which name characters, bind to
    the right description.
    """
    parts: list[str] = []
    lora = char.get("lora")
    if lora and lora.get("trigger_word"):
        parts.append(lora["trigger_word"].strip())
    desc = (char.get("visual_description") or "").strip()
    if desc:
        name = _display_name(char)
        parts.append(f"{name} ({desc})" if name else desc)
    if parts:
        return ", ".join(parts)
    return (char.get("identifier") or "").strip()
