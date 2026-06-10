"""enriched JSON → prompt text files (plan §13.5).

Reads each ``<project>/enriched/*.json`` and writes a paired
``<project>/prompts/<panel_id>.txt`` (positive prompt) and
``<panel_id>.neg.txt`` (negative prompt). Returns
``{panel_id: (prompt, neg_prompt)}``.

The prompt is a comma-separated string composed in a fixed order:

    style_prefix, style_trigger, char_triggers_or_descriptions,
    action, shot_hint, mood, setting: loc_description

This is a starting template tuned for Flux-family models (natural prose
with comma separators, LoRA triggers up front). The workflow expects the
user to review and edit the ``.txt`` files between this step and image
generation (plan §6), so the template is a sane default, not a final word.

For each character: if it has a LoRA trigger word, the trigger word is
used (the LoRA encodes appearance); otherwise the visual description is
used; otherwise the bare identifier.

Empty / missing fields are silently skipped. The negative prompt is the
same default for every panel; users edit per-panel as needed.
"""

from __future__ import annotations

import json
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


def build_prompts(project: Project) -> dict[str, tuple[str, str]]:
    """Build prompt + negative-prompt files for every enriched panel.

    Returns ``{panel_id: (prompt, negative_prompt)}``. Side-effect: writes
    ``<project>/prompts/<panel_id>.txt`` and ``<panel_id>.neg.txt`` per
    panel, in sorted panel-id order.
    """
    style = _try_get_style(project)

    results: dict[str, tuple[str, str]] = {}
    for path in sorted(project.enriched_dir.glob("*.json")):
        panel = json.loads(path.read_text(encoding="utf-8"))
        prompt, neg = _build_prompt(panel, style)

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


def _char_to_prompt_text(char: dict[str, Any]) -> str:
    """Trigger word and/or visual description for one character.

    - LoRA present and description set → both, comma-joined (LoRA pins
      identity, description carries clothing/state/etc. that the LoRA
      typically doesn't reliably capture).
    - LoRA only → trigger word.
    - Description only → description.
    - Neither → bare identifier.
    """
    parts: list[str] = []
    lora = char.get("lora")
    if lora and lora.get("trigger_word"):
        parts.append(lora["trigger_word"].strip())
    desc = (char.get("visual_description") or "").strip()
    if desc:
        parts.append(desc)
    if parts:
        return ", ".join(parts)
    return (char.get("identifier") or "").strip()
