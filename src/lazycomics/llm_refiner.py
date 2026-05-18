"""LLM prompt refinement (plan §13.6).

Refines per-panel prompts by sending each through an OpenAI-compatible
chat-completions endpoint. The single integration covers OpenAI proper,
Ollama, LM Studio, llama.cpp server, vLLM, and any other service that
speaks the OpenAI API surface.

System-prompt precedence: function arg → ``llm.system_prompt`` in config →
:data:`DEFAULT_SYSTEM_PROMPT`. The first refinement of each panel writes
the original to ``.txt.bak``; subsequent re-runs do not overwrite that
backup, so the *original* original is always preserved as the user's
safety net.
"""

from __future__ import annotations

import json
import shutil
from typing import Any

from lazycomics.config import cfg_get, load_config
from lazycomics.models import Project

__all__ = ["refine_prompts_with_llm", "DEFAULT_SYSTEM_PROMPT"]


DEFAULT_SYSTEM_PROMPT = """\
You refine image-generation prompts for comic panels. The target is a Flux-family model.

INPUT
A comma-separated prompt that may include, in any order: an art style
descriptor, LoRA trigger tokens (short codes following the pattern
``<name>_v<n>``, e.g. for a character or style LoRA), character
descriptions, an action, a camera/shot hint, a mood, and a setting (often
prefixed "setting:").

OUTPUT RULES
- Output the refined prompt and nothing else: no preamble, no surrounding
  quotes, no explanation, no labels.
- Preserve every LoRA trigger token VERBATIM. They are model-recognised
  tokens, not English words — do not paraphrase, translate, or replace them.
- Preserve every factual element: who is in the panel, what they are doing,
  where, camera/shot, mood. Do not change a verb's meaning.
- Do NOT invent new details (colours, lighting, props, weather, expressions)
  that are not already present in the input.
- Do NOT add generic quality boilerplate ("highly detailed", "8k",
  "masterpiece", "trending on artstation"). Flux ignores these and they
  dilute attention on the meaningful tokens.
- Keep the subject and action near the start of the prompt — Flux weights
  early tokens more heavily.
- Aim for under 75 words. Concise prompts perform better with Flux.
- Respond in English.

If the input is empty, output an empty string.
"""


def refine_prompts_with_llm(
    project: Project,
    panels: list[str] | None = None,
    *,
    system_prompt: str | None = None,
    backup: bool = True,
) -> dict[str, tuple[str, str]]:
    """Refine prompt ``.txt`` files via an OpenAI-compatible LLM.

    Parameters
    ----------
    project
        The lazycomics project.
    panels
        Panel IDs to refine. ``None`` refines every ``<id>.txt`` in
        ``prompts/`` (excluding ``.neg.txt``).
    system_prompt
        Overrides any config-level setting. ``None`` falls back to
        ``llm.system_prompt`` in config, then to
        :data:`DEFAULT_SYSTEM_PROMPT`.
    backup
        If ``True`` (default), the original ``.txt`` is copied to
        ``.txt.bak`` *the first time* the panel is refined. Subsequent
        refinements do not overwrite the ``.bak``.

    Returns
    -------
    dict
        ``{panel_id: (original, refined)}`` for panels that were
        successfully refined. Panels whose LLM calls fail keep their
        original ``.txt`` and are absent from the returned dict.

    Raises
    ------
    ValueError
        If ``llm.url`` or ``llm.model`` is missing from config.
    """
    cfg = load_config()
    url = cfg_get(cfg, "llm.url", None)
    model = cfg_get(cfg, "llm.model", None)
    api_key = cfg_get(cfg, "llm.api_key", None)

    if not url:
        raise ValueError(
            "LLM url not configured: set llm.url in lazycomics_config.yaml"
        )
    if not model:
        raise ValueError(
            "LLM model not configured: set llm.model in lazycomics_config.yaml"
        )

    if system_prompt is None:
        system_prompt = cfg_get(cfg, "llm.system_prompt", DEFAULT_SYSTEM_PROMPT)

    if panels is None:
        panel_ids = sorted(
            p.stem for p in project.prompts_dir.glob("*.txt")
            if not p.name.endswith(".neg.txt")
        )
    else:
        panel_ids = list(panels)

    endpoint = url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    results: dict[str, tuple[str, str]] = {}
    for panel_id in panel_ids:
        pair = _refine_one(
            project, panel_id, endpoint, headers, model, system_prompt, backup,
        )
        if pair is not None:
            results[panel_id] = pair
    return results


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _refine_one(
    project: Project,
    panel_id: str,
    endpoint: str,
    headers: dict[str, str],
    model: str,
    system_prompt: str,
    backup: bool,
) -> tuple[str, str] | None:
    txt_path = project.prompts_dir / f"{panel_id}.txt"
    if not txt_path.is_file():
        print(f"[refine] skipping {panel_id}: no prompt at {txt_path}")
        return None

    original = txt_path.read_text(encoding="utf-8").rstrip()
    if not original:
        print(f"[refine] skipping {panel_id}: prompt is empty")
        return None

    context = _format_context_from_enriched(project, panel_id)
    user_message = _format_user_message(original, context)

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.3,
    }

    refined = _call_llm(endpoint, headers, body)
    if refined is None:
        print(f"[refine] {panel_id}: LLM call failed, keeping original")
        return None

    refined = refined.strip()
    if not refined:
        print(f"[refine] {panel_id}: LLM returned empty content, keeping original")
        return None

    bak_path = project.prompts_dir / f"{panel_id}.txt.bak"
    if backup and not bak_path.is_file():
        shutil.copy(txt_path, bak_path)

    txt_path.write_text(refined + "\n", encoding="utf-8")
    return original, refined


def _format_context_from_enriched(project: Project, panel_id: str) -> str:
    """Render the panel's structured fields as a brief context block.

    Returns the empty string if no enriched JSON exists for this panel —
    refinement still works, just with less context.
    """
    enriched_path = project.enriched_dir / f"{panel_id}.json"
    if not enriched_path.is_file():
        return ""

    panel = json.loads(enriched_path.read_text(encoding="utf-8"))

    lines: list[str] = []
    if panel.get("action"):
        lines.append(f"- Action: {panel['action']}")
    if panel.get("shot_hint"):
        lines.append(f"- Shot: {panel['shot_hint']}")
    if panel.get("mood"):
        lines.append(f"- Mood: {panel['mood']}")
    if panel.get("loc_description"):
        lines.append(f"- Setting: {panel['loc_description']}")

    chars = panel.get("characters", [])
    if chars:
        descs = []
        for c in chars:
            ident = c.get("identifier", "")
            desc = (c.get("visual_description") or "").strip()
            descs.append(f"{ident} ({desc})" if desc else ident)
        lines.append(f"- Characters: {', '.join(descs)}")

    return "\n".join(lines)


def _format_user_message(prompt: str, context: str) -> str:
    if context:
        return f"Panel context:\n{context}\n\nPrompt to refine:\n{prompt}"
    return f"Prompt to refine:\n{prompt}"


def _call_llm(endpoint: str, headers: dict[str, str], body: dict[str, Any]) -> str | None:
    """POST to a chat-completions endpoint; return the message content or ``None``.

    ``requests`` is imported lazily — it's in the ``[llm]`` extras, not a
    hard dep. Errors at any stage (network, HTTP status, JSON shape)
    return ``None`` so the caller can preserve the original prompt rather
    than blanking it out.

    Module-level function so tests can monkeypatch it directly.
    """
    try:
        import requests
    except ImportError as e:
        raise RuntimeError(
            "refine_prompts_with_llm needs the 'requests' package: "
            "pip install lazycomics[llm]"
        ) from e

    try:
        response = requests.post(endpoint, headers=headers, json=body, timeout=60)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"[refine] HTTP error: {e}")
        return None

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        print(f"[refine] unexpected response shape: {e}")
        return None
