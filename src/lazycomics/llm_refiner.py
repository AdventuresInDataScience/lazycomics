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

Each refined prompt is checked against the system prompt's rules that code
can see (:func:`_rule_breaks`). One that breaks any is retried once with the
reasons; if the retry still breaks one, the original prompt is kept.
"""

from __future__ import annotations

import json
import re
import shutil
from typing import Any

from lazycomics.config import cfg_get, load_config
from lazycomics.models import Project
from lazycomics.prompt_builder import SIGNAGE, group_cue

__all__ = ["refine_prompts_with_llm", "DEFAULT_SYSTEM_PROMPT"]

# Per-request timeout. The first request also pays for a local server
# loading the model from disk, which took over 60s for the first three
# panels of the 2026-11-01 A/B run (gemma4 8B on Ollama), so they were
# silently left unrefined.
_TIMEOUT_S = 300


DEFAULT_SYSTEM_PROMPT = """\
You refine image-generation prompts for comic panels. The target is a Flux-family model.

INPUT
A comma-separated prompt that may include, in any order: an art style
descriptor, LoRA trigger tokens (short codes following the pattern
``<name>_v<n>``, e.g. for a character or style LoRA), character
descriptions, an action, a camera/shot hint, a mood, and a setting (often
prefixed "setting:"). It may come with panel context: the fields it was
built from, plus the panel's dialogue and sound effects.

OUTPUT RULES
- Output the refined prompt and nothing else: no preamble, no surrounding
  quotes, no explanation, no labels.
- Preserve every LoRA trigger token VERBATIM. They are model-recognised
  tokens, not English words — do not paraphrase, translate, or replace them.
- Preserve every factual element: who is in the panel, what they are doing,
  where, camera/shot, mood. Do not change a verb's meaning.
- Keep each character's appearance description VERBATIM, next to that
  character: age, hair colour and style, skin tone, ethnicity, build,
  clothing. Never shorten, paraphrase or drop any of it, even when the panel
  is a close-up or the description seems long. It is the only way the image
  model tells the characters apart; losing "young" or "brown hair" changes
  who gets drawn.
- If the input has a phrase like "four people, left to right: A, B, C and
  D, each clearly visible", keep it VERBATIM, word for word and in the same
  order, at the start of the prompt. It makes the image model draw everyone
  in the panel, in the order the lettering expects.
- Do NOT invent new details (colours, lighting, props, weather, expressions)
  that are not already present in the input or implied by its dialogue and
  sound effects.
- Dialogue and sound effects are lettered onto the panel afterwards. Never
  quote or paraphrase their words, and never mention speech bubbles,
  captions, signs or any written text: the image model paints text it is
  given as garbled lettering. Use them only for what they show. If a line or
  a sound implies an action, gesture or expression the prompt doesn't
  already describe, describe it in plain words: a "KNOCK KNOCK" sound effect
  means someone knocking on a door; a shouted line means a shouting face.
- Do NOT add generic quality boilerplate ("highly detailed", "8k",
  "masterpiece", "trending on artstation"). Flux ignores these and they
  dilute attention on the meaningful tokens.
- Keep the subject and action near the start of the prompt — Flux weights
  early tokens more heavily.
- Keep everything else concise: aim for under 75 words besides the character
  descriptions.
- Respond in English.

If the input is empty, output an empty string.
"""

# Follow-up when a refined prompt breaks a rule _rule_breaks can see.
_RETRY = (
    "That prompt breaks these rules: {}. Rewrite it to fix them, changing "
    "nothing else. Output the prompt and nothing else."
)


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
        # `llm.system_prompt: null` in config means "use the built-in default",
        # so coalesce an explicit null (which cfg_get returns verbatim) to it —
        # otherwise the system message goes out with null content and the
        # OpenAI-compat endpoint rejects the request ("invalid message content").
        system_prompt = cfg_get(cfg, "llm.system_prompt", None) or DEFAULT_SYSTEM_PROMPT

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

    enriched_path = project.enriched_dir / f"{panel_id}.json"
    panel = json.loads(enriched_path.read_text(encoding="utf-8")) if enriched_path.is_file() else {}
    user_message = _format_user_message(original, _format_context(panel))

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

    broken = _rule_breaks(refined, original, panel)
    if broken:
        # One retry, told what broke; failing that, the original stands.
        print(f"[refine] {panel_id}: retrying ({', '.join(kind for kind, _ in broken)})")
        body["messages"] += [
            {"role": "assistant", "content": refined},
            {"role": "user", "content": _RETRY.format("; ".join(why for _, why in broken))},
        ]
        refined = (_call_llm(endpoint, headers, body) or "").strip()
        broken = _rule_breaks(refined, original, panel) if refined else broken
        if broken or not refined:
            print(f"[refine] {panel_id}: refined prompt still breaks the rules "
                  f"({'; '.join(why for _, why in broken) or 'empty reply'}), keeping original")
            return None

    bak_path = project.prompts_dir / f"{panel_id}.txt.bak"
    if backup and not bak_path.is_file():
        shutil.copy(txt_path, bak_path)

    txt_path.write_text(refined + "\n", encoding="utf-8")
    return original, refined


def _format_context(panel: dict[str, Any]) -> str:
    """Render the panel's structured fields as a brief context block.

    ``panel`` is the enriched JSON, or ``{}`` if there is none (returns ``""``:
    refinement still works, just with less context).
    """
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

    # Lettered later, never drawn: the LLM uses them only for the action they
    # imply (Docs/PLAN.md problem 25). The reminder sits beside the data
    # because a small local model follows it there more reliably.
    dialogue = [f'{d.get("character", "")} ({d.get("bubble_type", "speech")}): "{d["text"]}"'
                for d in panel.get("dialogue_lines", []) if d.get("text")]
    if dialogue:
        lines.append("- Dialogue (lettered later: show the expressions and actions it implies, "
                     f"never its words): {'; '.join(dialogue)}")
    sfx = [s["text"] for s in panel.get("sfx_lines", []) if s.get("text")]
    if sfx:
        lines.append("- Sound effects (lettered later: show the action that makes the sound, "
                     f"never the word): {'; '.join(sfx)}")

    return "\n".join(lines)


def _rule_breaks(refined: str, original: str, panel: dict[str, Any]) -> list[tuple[str, str]]:
    """The system prompt's rules that ``refined`` breaks and ``original`` kept.

    Returns ``(kind, why)`` pairs; ``why`` is sent back to the LLM on the
    retry. A small local model doesn't reliably follow a rule from the system
    prompt alone (W8), so every rule a check can see is checked here
    (Docs/PLAN.md, Reference, "Prompt rules"):

    - ``quoted``: it quotes the panel's lettering, which the image model would
      paint (problem 8). A sound effect counts if it appears as written
      (case-sensitive, so "KRRK" but not "phones ring" for RING); a dialogue
      line of three or more words if it appears whole, ignoring case and
      punctuation.
    - ``cue``: it drops or rewords ``prompt_builder.group_cue``, which makes
      the image model draw the cast in the order the lettering expects
      (problem 16).
    - ``description``: it changes a character's description, which is how
      the image model tells the cast apart (W4).
    - ``signage``: it adds signs, posters, banners or billboards, which come
      out as gibberish lettering (problem 7).
    """
    def norm(s: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s.lower()).split())

    def signs(s: str) -> set[str]:
        return {m.group(0).lower().removesuffix("s") for m in SIGNAGE.finditer(s)}

    broken: list[tuple[str, str]] = []
    lettered = [t for s in panel.get("sfx_lines", [])
                if (t := (s.get("text") or "").strip()) and t in refined and t not in original]
    for d in panel.get("dialogue_lines", []):
        line = norm(d.get("text") or "")
        if len(line.split()) >= 3 and line in norm(refined) and line not in norm(original):
            lettered.append(d["text"].strip())
    broken += [("quoted", f'it quotes "{t}", which is lettered onto the panel later: describe '
                          "only what it shows, the action, gesture or expression") for t in lettered]

    cue = group_cue(panel)
    if cue and norm(cue) in norm(original) and norm(cue) not in norm(refined):
        broken.append(("cue", f'it drops or rewords "{cue}": keep that phrase word for word'))

    for c in panel.get("characters", []):
        desc = (c.get("visual_description") or "").strip()
        if desc and norm(desc) in norm(original) and norm(desc) not in norm(refined):
            broken.append(("description", f"it changes {c.get('identifier')}'s description: "
                                          f'keep "{desc}" word for word'))

    broken += [("signage", f'it adds "{word}", which the image model fills with gibberish '
                           "lettering: describe light and shape instead")
               for word in sorted(signs(refined) - signs(original))]
    return broken


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
        response = requests.post(endpoint, headers=headers, json=body, timeout=_TIMEOUT_S)
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
