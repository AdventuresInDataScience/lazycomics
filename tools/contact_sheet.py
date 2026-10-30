#!/usr/bin/env python3
"""CLI: contact sheets + machine checks for an existing project.

Usage::

    python tools/contact_sheet.py <project_dir> [--out DIR] [--baseline PROJECT_DIR]

Reads only what the pipeline already wrote (it generates and re-letters
nothing) and writes into ``--out`` (default: ``<project_dir>``):

* ``sheet_pages.png``: per page, one row per panel (raw | lettered | as placed
  on the page) with its issues, and the assembled page alongside;
* ``sheet_characters.png``: each character's reference beside every
  appearance.

It also prints the per-panel checks from ``lazycomics.review.check_panel``.
Works on any project, e.g. ``test_e2e_output/e2e/e2e`` after a full e2e
run; ``tools/golden_run.py`` calls ``write_sheets`` for the golden subset.
Needs the project to have been through ``render_text`` and
``assemble_pages`` for the layout and slot records.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Allow running straight from a source checkout without installing the package.
sys.path.insert(0, str(REPO / "src"))

from lazycomics import review  # noqa: E402
from lazycomics.models import Project  # noqa: E402
from lazycomics.project import load_project  # noqa: E402


def write_sheets(
    project: Project,
    out_dir: Path,
    *,
    baseline: Project | None = None,
) -> dict[str, list[review.Issue]]:
    """Check every enriched panel and write both sheets into ``out_dir``.

    Returns ``{panel_id: issues}``.
    """
    panel_ids = sorted((p.stem for p in project.enriched_dir.glob("*.json")),
                       key=review.panel_sort_key)
    issues = {pid: review.check_panel(project, pid) for pid in panel_ids}
    review.pages_sheet(project, panel_ids, out_dir / "sheet_pages.png",
                       baseline=baseline, issues=issues)
    review.character_sheet(project, panel_ids, out_dir / "sheet_characters.png")
    return issues


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("project_dir")
    ap.add_argument("--out", help="where to write the sheets (default: the project dir)")
    ap.add_argument("--baseline", help="another project dir to compare against")
    args = ap.parse_args(argv)

    project = load_project(args.project_dir)
    out = Path(args.out).resolve() if args.out else project.base_dir
    baseline = load_project(args.baseline) if args.baseline else None
    issues = write_sheets(project, out, baseline=baseline)

    clean = sum(1 for v in issues.values() if not v)
    print(f"checks: {clean}/{len(issues)} panels clean")
    for pid, found in issues.items():
        for i in found:
            print(f"  {pid}: {i}")
    print(f"sheets: {out / 'sheet_pages.png'}\n        {out / 'sheet_characters.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
