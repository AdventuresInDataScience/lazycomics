#!/usr/bin/env python3
"""CLI: regenerate the golden subset and write its contact sheets.

Usage::

    python tools/golden_run.py [--out NAME] [--force] [--no-generate]
                               [--base-resolution N] [--baseline NAME]
                               [--fresh-prompts | --refine] [--set KEY=VALUE ...]
                               [--lora CHARACTER=PATH ...]

The golden subset is ``tests/e2e/fixtures/golden.cbml``: three page blocks
cut from the e2e story (see ``GOLDEN_PAGES``) with the story's registered
characters, locations and style. One command runs
enrich -> prompts -> refs -> generate -> render_text -> assemble_pages and
writes into the run folder ``tests/e2e/outputs/golden/<NAME>/`` (default
``latest``; ``--out`` also takes a path):

* ``report.txt``: per-panel checks (``lazycomics.review.check_panel``:
  aspect, cropped-off text, reading order, overlaps, tails) plus generation
  time;
* ``sheet_pages.png`` / ``sheet_characters.png``: see
  ``tools/contact_sheet.py``.

Iterating:

* Generated panels are kept between runs (skip-if-exists), so text/bubble
  work re-letters in seconds. **Pass --force after changing anything that
  affects generation** (bridge, refs, prompts, wan2gp config); otherwise the
  stale panels are reused.
* ``--no-generate`` never starts Wan2GP: re-letter, re-assemble, re-check.
* Prompts are the frozen LLM-refined set in
  ``tests/e2e/fixtures/golden_prompts/``, and the seed is fixed by config,
  so two runs differ only by what you changed. ``--fresh-prompts`` uses
  ``build_prompts`` output instead; ``--refine`` also runs it through the
  LLM refiner, as the full pipeline does. LLM output varies run to run, so
  to A/B anything else against refined prompts, freeze them first: copy
  ``<run folder>/project/prompts/*.txt`` over ``golden_prompts/``.
* ``--baseline NAME`` (another run's name, or a path such as an archived run
  in ``tests/e2e/archived_test_outputs/``) adds that run's lettered panels as
  a column, and its character crops under this run's, for A/B review.
* ``--set KEY=VALUE`` overrides any config value for this run, e.g.
  ``--set wan2gp.default_steps=8`` (repeatable; values parse as YAML).
  ``--base-resolution N`` is shorthand for ``--set wan2gp.base_resolution=N``
  (e.g. 768 for a faster loop). Combine either with ``--force``.
* ``--lora CHARACTER=PATH`` attaches a trained LoRA (``tools/train_lora.py``)
  to that character for this run, at ``ai_toolkit.lora_weight`` (so
  ``--set ai_toolkit.lora_weight=0.8`` A/Bs the weight), trigger word = the
  character's ID. Repeatable. Under the default
  ``enricher.multi_char_strategy: auto``, that character's multi-character
  panels go down the inpaint path, whose settings don't reach Wan2GP yet
  (Docs/PLAN.md problem 6): add ``--set enricher.multi_char_strategy=single``
  or judge solo panels only.
"""

from __future__ import annotations

import argparse
import copy
import shutil
import sys
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
# Allow running straight from a source checkout without installing the package.
sys.path.insert(0, str(REPO / "src"))

import lazycomics as lc  # noqa: E402
from lazycomics import review  # noqa: E402
from lazycomics.config import cfg_get, load_config  # noqa: E402

from contact_sheet import write_sheets  # noqa: E402  (sibling script in tools/)

FIXTURES = REPO / "tests" / "e2e" / "fixtures"
GOLDEN_CBML = FIXTURES / "golden.cbml"
FROZEN_PROMPTS = FIXTURES / "golden_prompts"
RUNS = REPO / "tests" / "e2e" / "outputs" / "golden"  # gitignored

# Golden page -> where it comes from in story.cbml and what it covers
# (Docs/PLAN.md problem numbers).
GOLDEN_PAGES = {
    1: "story page 3: Gerald + Yuki 4-line exchange (problem 3), four 0.44 "
       "solo panels incl. a Gerald close-up (consistency) and INTERN (no ref)",
    2: "story pages 8-9 (spread): 1.33 panels, 4-character panel_4 with 2 "
       "speakers (problem 4); exercises the spread split",
    4: "story page 10: 0.22 strip panels; narrow-panel text (problem 12)",
}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out = _run_dir(args.out)
    # Loaded first so a bad --baseline fails before a 10-minute generation.
    baseline = lc.load_project(_run_dir(args.baseline) / "project") if args.baseline else None

    config = copy.deepcopy(load_config(REPO / "lazycomics_config.yaml"))
    overrides = list(args.set)
    if args.base_resolution:
        overrides.append(f"wan2gp.base_resolution={args.base_resolution}")
    for item in overrides:
        _override(config, item)
    loras = [_lora_arg(item) for item in args.lora]  # checked before any work

    project = _open_project(out / "project")
    for name, path in loras:
        lc.set_lora(project, "character", name, path,
                    weight=cfg_get(config, "ai_toolkit.lora_weight", 1.0), trigger_word=name)
    panels = sorted((p.panel_id for p in lc.enrich(project, config=config)),
                    key=review.panel_sort_key)
    lc.build_prompts(project)
    if args.refine:
        lc.refine_prompts_with_llm(project, panels=panels)
        if not args.no_generate:
            _unload_llm(config)
    elif not args.fresh_prompts:
        for p in FROZEN_PROMPTS.glob("*.txt"):
            shutil.copy2(p, project.prompts_dir / p.name)
    lc.prepare_references(project, panels=panels, force=True, config=config)

    reused = [p for p in panels if (project.panels_dir / f"{p}.png").is_file()]
    gen_s = None
    if not args.no_generate:
        t0 = time.perf_counter()
        lc.generate_panels(project, panels=panels, force=args.force, config=config)
        gen_s = time.perf_counter() - t0

    lc.render_text(project, panels=panels, force=True, config=config)
    lc.assemble_pages(
        project,
        page_height_px=cfg_get(config, "assembly.page_height_px", None),
        gutter_px=cfg_get(config, "assembly.gutter_px", None),
        bg_color=cfg_get(config, "assembly.bg_color", None),
        stretch_tolerance=cfg_get(config, "assembly.stretch_tolerance", None),
        fit_mode=cfg_get(config, "assembly.fit_mode", None),
        force=True,
    )

    issues = write_sheets(project, out, baseline=baseline)

    report = _format_report(panels, issues, gen_s, [] if args.force else reused, args)
    (out / "report.txt").write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"sheets: {out / 'sheet_pages.png'}\n        {out / 'sheet_characters.png'}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="latest",
                    help=f"run name (a folder in {RUNS.relative_to(REPO)}) or a path")
    ap.add_argument("--force", action="store_true", help="regenerate every golden panel")
    ap.add_argument("--no-generate", action="store_true", help="never start Wan2GP")
    ap.add_argument("--base-resolution", type=int, help="override wan2gp.base_resolution")
    ap.add_argument("--baseline", help="another run's name or path, to compare against")
    prompts = ap.add_mutually_exclusive_group()
    prompts.add_argument("--fresh-prompts", action="store_true", help="skip the frozen prompts")
    prompts.add_argument("--refine", action="store_true",
                         help="fresh prompts refined by the LLM (llm.* config)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a config value, e.g. wan2gp.default_steps=8 (repeatable)")
    ap.add_argument("--lora", action="append", default=[], metavar="CHARACTER=PATH",
                    help="attach a trained LoRA to a character for this run (repeatable)")
    return ap.parse_args(argv)


def _lora_arg(item: str) -> tuple[str, Path]:
    """``CHARACTER=PATH`` -> (character, resolved path); the file must exist."""
    name, sep, raw = item.partition("=")
    path = Path(raw).expanduser().resolve()
    if not sep or not name or not path.is_file():
        raise SystemExit(f"--lora wants CHARACTER=PATH to an existing file, got {item!r}")
    return name, path


def _run_dir(arg: str) -> Path:
    """A bare name is a run folder under ``RUNS``; anything else is a path."""
    p = Path(arg)
    return RUNS / arg if len(p.parts) == 1 and not p.is_absolute() else p.resolve()


def _override(config: dict, item: str) -> None:
    """Apply one ``dotted.key=value`` to ``config`` in place (value parsed as YAML)."""
    key, sep, raw = item.partition("=")
    if not sep or not key:
        raise SystemExit(f"--set wants KEY=VALUE, got {item!r}")
    *parents, leaf = key.split(".")
    node = config
    for k in parents:
        node = node.setdefault(k, {})
    node[leaf] = yaml.safe_load(raw)


def _unload_llm(config: dict) -> None:
    """Free the refiner's VRAM before Wan2GP starts.

    Ollama keeps a model loaded for 5 minutes after the last request, which
    would leave Wan2GP short of VRAM and skew the generation time. Uses
    Ollama's native API; for any other server this just fails harmlessly.
    """
    base = cfg_get(config, "llm.url", "").rstrip("/").removesuffix("/v1")
    try:
        import requests
        requests.post(f"{base}/api/generate", timeout=30,
                      json={"model": cfg_get(config, "llm.model", ""), "keep_alive": 0})
    except Exception as e:  # noqa: BLE001 — best effort
        print(f"[golden] couldn't unload the LLM ({e}); generation may be slower")


def _open_project(project_dir: Path) -> lc.Project:
    """Load the golden project, creating it from the fixtures on first run.

    The CBML and assets are refreshed from the fixtures every run (asset
    registration is idempotent), so fixture edits are picked up without
    wiping generated panels.
    """
    if (project_dir / "manifest.json").is_file():
        project = lc.load_project(project_dir)
        shutil.copy2(GOLDEN_CBML, project.cbml_path)
    else:
        project_dir.parent.mkdir(parents=True, exist_ok=True)
        project = lc.create_project(project_dir.name, GOLDEN_CBML,
                                    base_dir=project_dir.parent)

    for name in ["DAVID", "GERALD", "INTERN", "RANJEET", "YUKI"]:
        png = FIXTURES / f"{name}.png"
        lc.register_character(project, name,
                              description=(FIXTURES / f"{name}.txt").read_text().strip(),
                              reference_images=[png] if png.exists() else [])
    for name in ["GERALDS_APARTMENT", "THE_BAR", "THE_OFFICE"]:
        png = FIXTURES / f"{name}.png"
        lc.register_location(project, name,
                             description=(FIXTURES / f"{name}.txt").read_text().strip(),
                             reference_images=[png] if png.exists() else [])
    lc.register_style(project, prompt_prefix=(FIXTURES / "Style.txt").read_text().strip(),
                      reference_images=[FIXTURES / "Style.png"])
    return project


def _format_report(panels, issues, gen_s, reused, args) -> str:
    lines = ["golden run: " + time.strftime("%Y-%m-%d %H:%M")]
    flags = [f"--{k.replace('_', '-')}={item}" for k, v in vars(args).items()
             if v not in (None, False) and k != "out"
             for item in (v if isinstance(v, list) else [v])]
    lines.append("flags: " + (" ".join(flags) or "(none)"))
    if gen_s is None:
        lines.append("generation: skipped (--no-generate)")
    else:
        fresh = len(panels) - len(reused)
        lines.append(f"generation: {gen_s:.0f}s for {fresh} panel(s)"
                     + (f"; reused {len(reused)} existing (pass --force to regenerate)" if reused else ""))
    clean = sum(1 for p in panels if not issues[p])
    lines.append(f"checks: {clean}/{len(panels)} panels clean")
    for page, about in GOLDEN_PAGES.items():
        lines.append(f"\npage {page}  ({about})")
        for pid in (p for p in panels if review.panel_sort_key(p)[0] == page):
            found = issues[pid]
            lines.append(f"  {pid}: " + ("ok" if not found else str(found[0])))
            lines += [f"  {' ' * len(pid)}  {i}" for i in found[1:]]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
