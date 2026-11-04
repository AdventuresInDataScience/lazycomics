#!/usr/bin/env python3
"""CLI: refiner bench — run the LLM refiner on scripts it wasn't tuned on and
tally which of its rules it breaks.

Usage::

    python tools/refiner_bench.py [--sets synthetic,holdout] [--seed 1] [--panels 16]

The refiner's rules (``llm_refiner.DEFAULT_SYSTEM_PROMPT``) were worked out
on the e2e story. This checks they hold on other scripts. It needs the LLM
server (``llm.*`` config) but not Wan2GP:

* ``synthetic``: a seeded random script (:func:`synthetic_script`) with its
  own cast and descriptions, locations, actions, shots, dialogue of every
  bubble type and SFX, none taken from the e2e story. Casts of 1–4, so some
  panels get the group cue and some three-character panels don't.
* ``holdout``: ``tests/fixtures/holdout_story/``, a short hand-written
  script with a different cast, setting and style.

Each set runs ``enrich`` → ``build_prompts`` → ``refine_prompts_with_llm``.
Every LLM reply is recorded and the first is scored with
``llm_refiner._rule_breaks``, so the table shows how often each rule is
broken first time, how often the retry fixes it, and how often the original
prompt is kept. It also counts how often a refined prompt starts with the
group cue, which the system prompt asks for but nothing checks. Each panel's
built prompt, first reply and final prompt go to
``tests/outputs/refiner_bench/<set>.txt``. About 23s a panel with
gemma4 8B on Ollama, so ~9 min for both sets; run from the repo root so the
config is found.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Allow running straight from a source checkout without installing the package.
sys.path.insert(0, str(REPO / "src"))

import lazycomics as lc  # noqa: E402
from lazycomics import llm_refiner, review  # noqa: E402
from lazycomics.config import cfg_get, load_config  # noqa: E402
from lazycomics.prompt_builder import group_cue  # noqa: E402

from ab_run import register_story_assets  # noqa: E402  (sibling script in tools/)

HOLDOUT_STORY = REPO / "tests" / "fixtures" / "holdout_story"
OUT = REPO / "tests" / "outputs" / "refiner_bench"

# Table rows, in order.
ROWS = ["panels", "clean first time", "  quoted", "  cue", "  description", "  signage",
        "  no reply", "retried", "  fixed by the retry", "kept the original",
        "group-cue panels", "  refined prompt starts with it (not checked)"]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = load_config(REPO / "lazycomics_config.yaml")
    results: dict[str, collections.Counter] = {}
    for name in args.sets.split(","):
        if name == "synthetic":
            project = synthetic_script(OUT / "synthetic", args.seed, args.panels)
        elif name == "holdout":
            project = _fresh_project(OUT / "holdout", HOLDOUT_STORY / "story.cbml")
            register_story_assets(project, HOLDOUT_STORY)
        else:
            raise SystemExit(f"unknown set {name!r}: use synthetic and/or holdout")
        results[name] = bench(project, OUT / f"{name}.txt")

    print(f"\nrefiner bench, llm.model = {cfg_get(config, 'llm.model', '?')}")
    print(f"{'':<48}" + "".join(f"{name:>12}" for name in results))
    for row in ROWS:
        print(f"{row:<48}" + "".join(f"{results[n][row.strip()]:>12}" for n in results))
    print(f"per-panel prompts: {OUT}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sets", default="synthetic,holdout", help="comma-separated: synthetic, holdout")
    ap.add_argument("--seed", type=int, default=1, help="synthetic script seed")
    ap.add_argument("--panels", type=int, default=16, help="synthetic script length")
    return ap.parse_args(argv)


def bench(project: lc.Project, report: Path) -> collections.Counter:
    """Build and refine every panel; tally rule breaks and write ``report``."""
    lc.enrich(project)
    lc.build_prompts(project)
    panels = {p.stem: json.loads(p.read_text(encoding="utf-8"))
              for p in project.enriched_dir.glob("*.json")}
    built = {pid: (project.prompts_dir / f"{pid}.txt").read_text(encoding="utf-8").rstrip()
             for pid in panels}

    # Every reply, keyed by the panel's first user message (a retry resends it).
    replies: dict[str, list[str | None]] = collections.defaultdict(list)
    call_llm = llm_refiner._call_llm

    def recording(endpoint, headers, body):
        reply = call_llm(endpoint, headers, body)
        replies[body["messages"][1]["content"]].append(reply)
        return reply

    llm_refiner._call_llm = recording
    try:
        lc.refine_prompts_with_llm(project)
    finally:
        llm_refiner._call_llm = call_llm

    tally: collections.Counter = collections.Counter()
    lines: list[str] = []
    for pid in sorted(panels, key=review.panel_sort_key):
        panel, original = panels[pid], built[pid]
        got = replies[llm_refiner._format_user_message(original, llm_refiner._format_context(panel))]
        first = (got[0] or "").strip() if got else ""
        final = (project.prompts_dir / f"{pid}.txt").read_text(encoding="utf-8").rstrip()
        breaks = llm_refiner._rule_breaks(first, original, panel) if first else []
        cue = group_cue(panel)

        tally["panels"] += 1
        if not first:
            tally["no reply"] += 1
        elif not breaks:
            tally["clean first time"] += 1
        tally.update({kind for kind, _ in breaks})
        if len(got) > 1:
            tally["retried"] += 1
            tally["fixed by the retry"] += final != original
        tally["kept the original"] += final == original
        if cue:
            tally["group-cue panels"] += 1
            tally["refined prompt starts with it (not checked)"] += (
                final != original and _norm(final).startswith(_norm(cue)))

        outcome = ("kept the original" if final == original
                   else "refined after a retry" if len(got) > 1 else "refined")
        lines += [f"== {pid}" + ("  (group cue)" if cue else ""), f"built: {original}",
                  f"first: {first or '(no reply)'}"]
        lines += [f"  breaks {kind}: {why}" for kind, why in breaks]
        lines += [f"final ({outcome}): {final}", ""]
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")
    return tally


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s.lower()).split())


def _fresh_project(project_dir: Path, cbml: Path) -> lc.Project:
    if project_dir.exists():
        shutil.rmtree(project_dir)
    project_dir.parent.mkdir(parents=True, exist_ok=True)
    return lc.create_project(project_dir.name, cbml, base_dir=project_dir.parent)


# ---------------------------------------------------------------------------
# Synthetic scripts: nothing here comes from the e2e story.
# ---------------------------------------------------------------------------


_CAST = {
    "MIRA": "young woman in her twenties, dark brown skin, long black braids, yellow raincoat over grey overalls",
    "TOMAS": "man in his forties, olive skin, shaved head, thick black beard, green fisherman's sweater",
    "IRIS": "elderly woman in her seventies, pale skin, short white curls, round glasses, purple cardigan",
    "KOJO": "teenage boy, dark skin, short twists, red hoodie and blue jeans",
    "LENA": "woman in her thirties, freckled pale skin, ginger bob, white lab coat over a blue jumper",
    "SAUL": "tall man in his sixties, tanned skin, long grey ponytail, black leather waistcoat",
}
_PLACES = {
    "HARBOUR": "small fishing harbour at dawn, wooden jetties, moored boats, gulls, low fog over grey water",
    "LIBRARY": "old public library, tall wooden shelves, green reading lamps, dust in the light from high windows",
    "LAB": "cramped university lab, glass beakers, humming fridges, a whiteboard covered in diagrams",
    "MARKET": "busy covered market, fruit stalls, hanging lamps, crates stacked in the aisles",
    "TRAIN": "night train carriage, worn velvet seats, rain on the windows, flickering overhead lights",
}
_STYLE = "ink and watercolour comic art, loose linework, soft washes of colour"
# Indexed by cast size (3 = three or more); {0}, {1}, {2} are cast names.
_ACTIONS = {
    1: ["{0} reads a crumpled letter by the window.", "{0} hammers on a locked door.",
        "{0} slumps in a chair, exhausted.", "{0} sprints through the crowd, out of breath.",
        "{0} drops a tray of glasses.", "{0} peers at a faded photograph."],
    2: ["{0} hands {1} a small wrapped box.", "{0} and {1} argue across a table.",
        "{1} pulls {0} back from the edge.", "{0} whispers something to {1}.",
        "{0} and {1} pore over a map."],
    3: ["The group huddles around a map.", "Everyone freezes as the lights go out.",
        "{0} bursts in while the others turn to look.", "They walk side by side without speaking.",
        "{0} and {1} hold {2} back from the door."],
}
_SHOTS = ["close-up", "medium shot", "wide establishing shot", "over-the-shoulder shot",
          "low angle", "high angle looking down"]
_MOODS = ["tense", "comedic", "quiet and sad", "frantic", "warm"]
_LINES = ["You said you'd be here an hour ago.", "Don't touch that.", "Is anyone else hearing this?",
          "We have to leave right now.", "Fine.", "I told you it would rain.", "Who left the door open?",
          "That is not what I meant and you know it.", "Look behind you", "Maybe tomorrow.",
          "Say that again.", "I found it under the floorboards.", "Nobody follows us.", "Wait."]
_SFX = ["KNOCK KNOCK", "CRASH", "SLAM", "BZZZT", "SPLASH", "CREAK", "HONK", "RIIING"]
_SFX_POS = ["top-left", "top-right", "center", "bottom-left", "bottom-right"]


def synthetic_script(project_dir: Path, seed: int, panels: int) -> lc.Project:
    """Write a seeded random script and register its assets; return its project."""
    rng = random.Random(seed)
    lines = [f"## Synthetic refiner bench, seed {seed}", "aspect: us-comic", ""]
    for i in range(panels):
        if i % 4 == 0:
            lines += ["PAGE preset:grid-2x2", ""]
        cast = rng.sample(sorted(_CAST), rng.choices([1, 2, 3, 4], [3, 4, 3, 2])[0])
        action = rng.choice(_ACTIONS[min(len(cast), 3)]).format(*(c.title() for c in cast))
        lines += [f"PANEL {'ABCD'[i % 4]}", f"loc: {rng.choice(sorted(_PLACES))}",
                  f"chars: {', '.join(cast)}", f"shot: {rng.choice(_SHOTS)}",
                  f"mood: {rng.choice(_MOODS)}", f"> {action}"]
        if rng.random() < 0.4:
            lines.append(f"[sfx pos:{rng.choice(_SFX_POS)} color:#aa0000] {rng.choice(_SFX)}")
        # Speakers may repeat, so some group panels have only one.
        for speaker in rng.choices(cast, k=rng.choices([0, 1, 2, 3], [3, 3, 4, 2])[0]):
            text = rng.choice(_LINES)
            mark = rng.choices(['"', "~", "!", "?"], [6, 2, 2, 1])[0]
            lines.append({'"': f'{speaker}: "{text}"', "~": f"{speaker}: ~{text}~",
                          "!": f'{speaker}: !"{text.upper()}!"!', "?": f'{speaker}: ?"{text}"?'}[mark])
        lines.append("")

    cbml = project_dir.parent / f"{project_dir.name}.cbml"
    cbml.parent.mkdir(parents=True, exist_ok=True)
    cbml.write_text("\n".join(lines), encoding="utf-8")
    project = _fresh_project(project_dir, cbml)
    cbml.unlink()
    for name, desc in _CAST.items():
        lc.register_character(project, name, description=desc)
    for name, desc in _PLACES.items():
        lc.register_location(project, name, description=desc)
    lc.register_style(project, prompt_prefix=_STYLE)
    return project


if __name__ == "__main__":
    raise SystemExit(main())
