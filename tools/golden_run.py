#!/usr/bin/env python3
"""CLI: regenerate the golden subset and write its contact sheets.

Usage::

    python tools/golden_run.py [--out golden_output] [--force] [--no-generate]
                               [--base-resolution N] [--baseline DIR]
                               [--fresh-prompts]

The golden subset is ``tests/fixtures/integration/golden.cbml``: three page
blocks cut from the e2e story (see ``GOLDEN_PAGES``) with the story's
registered characters, locations and style. One command runs
enrich -> prompts -> refs -> generate -> render_text -> assemble_pages and
writes into ``<out>``:

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
  ``tests/fixtures/integration/golden_prompts/`` (from the 2026-06-10 e2e
  run) and the seed is fixed by config, so two runs differ only by what you
  changed. ``--fresh-prompts`` uses ``build_prompts`` output instead.
* ``--baseline <another --out dir>`` adds that run's lettered panels as a
  column, for A/B review of an experiment.
* ``--base-resolution`` overrides ``wan2gp.base_resolution`` (e.g. 768 for a
  faster loop); combine with ``--force``.
"""

from __future__ import annotations

import argparse
import copy
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Allow running straight from a source checkout without installing the package.
sys.path.insert(0, str(REPO / "src"))

import lazycomics as lc  # noqa: E402
from lazycomics import review  # noqa: E402
from lazycomics.config import cfg_get, load_config  # noqa: E402

from contact_sheet import write_sheets  # noqa: E402  (sibling script in tools/)

FIXTURES = REPO / "tests" / "fixtures" / "integration"
GOLDEN_CBML = FIXTURES / "golden.cbml"
FROZEN_PROMPTS = FIXTURES / "golden_prompts"

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
    out = Path(args.out).resolve()

    config = load_config(REPO / "lazycomics_config.yaml")
    if args.base_resolution:
        config = copy.deepcopy(config)
        config.setdefault("wan2gp", {})["base_resolution"] = args.base_resolution

    project = _open_project(out / "project")
    panels = sorted((p.panel_id for p in lc.enrich(project, config=config)),
                    key=review.panel_sort_key)
    lc.build_prompts(project)
    if not args.fresh_prompts:
        for p in FROZEN_PROMPTS.glob("*.txt"):
            shutil.copy2(p, project.prompts_dir / p.name)
    lc.prepare_references(project, panels=panels, force=True)

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

    baseline = lc.load_project(Path(args.baseline).resolve() / "project") if args.baseline else None
    issues = write_sheets(project, out, baseline=baseline)

    report = _format_report(panels, issues, gen_s, [] if args.force else reused, args)
    (out / "report.txt").write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"sheets: {out / 'sheet_pages.png'}\n        {out / 'sheet_characters.png'}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(REPO / "golden_output"))
    ap.add_argument("--force", action="store_true", help="regenerate every golden panel")
    ap.add_argument("--no-generate", action="store_true", help="never start Wan2GP")
    ap.add_argument("--base-resolution", type=int, help="override wan2gp.base_resolution")
    ap.add_argument("--baseline", help="another golden --out dir to compare against")
    ap.add_argument("--fresh-prompts", action="store_true", help="skip the frozen prompts")
    return ap.parse_args(argv)


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
    flags = [f"--{k.replace('_', '-')}={v}" for k, v in vars(args).items()
             if v not in (None, False) and k != "out"]
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
