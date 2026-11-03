#!/usr/bin/env python3
"""CLI: contact sheets + machine checks for an existing project.

Usage::

    python tools/contact_sheet.py <project_dir> [--reletter] [--out DIR]
                                  [--baseline PROJECT_DIR]

Reads what the pipeline already wrote (it never generates) and writes into
``--out`` (default: ``<project_dir>``):

* ``sheet_pages.png``: per page, one row per panel (raw | lettered | as placed
  on the page) with its issues, and the assembled page alongside;
* ``sheet_characters.png``: each character's reference beside a head crop
  from every appearance (with ``--baseline``, that run's crop beneath each).

It also prints the per-panel checks from ``lazycomics.review.check_panel``.
Works on any project, e.g. ``tests/e2e/outputs/e2e`` after a full e2e run;
``tools/golden_run.py`` calls ``write_sheets`` for the golden subset.
Needs the project to have been through ``render_text`` and
``assemble_pages`` for the layout and slot records.

``--reletter`` first re-runs ``render_text`` and ``assemble_pages`` over the
project's existing panels (forced, at the ``assembly.*`` page size from
``lazycomics_config.yaml``), so any project is a lettering regression set
with no GPU: e.g. the e2e's 33 panels, in seconds. It overwrites
``panels_text/`` and ``pages/``, hand edits included, and leaves the print
pages (``pages_print/``) as they were. Panels the project has upscaled
(``panels_upscaled/``) are lettered at that size, as the pipeline does.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# Allow running straight from a source checkout without installing the package.
sys.path.insert(0, str(REPO / "src"))

import lazycomics as lc  # noqa: E402
from lazycomics import review  # noqa: E402
from lazycomics.config import cfg_get, load_config  # noqa: E402
from lazycomics.models import Project  # noqa: E402
from lazycomics.project import load_project  # noqa: E402


def reletter(project: Project, config: dict, panels: list[str] | None = None) -> None:
    """Re-letter ``panels`` (default: all) and re-assemble every page, forced,
    at the page size ``config`` gives (``assembly.*``).

    The checks read the screen pages only, so print pages are skipped:
    ``pages_print/`` is left as it was."""
    lc.render_text(project, panels=panels, force=True, config=config)
    lc.assemble_pages(
        project,
        page_height_px=cfg_get(config, "assembly.page_height_px", None),
        print_page_height_px=0,
        gutter_px=cfg_get(config, "assembly.gutter_px", None),
        bg_color=cfg_get(config, "assembly.bg_color", None),
        stretch_tolerance=cfg_get(config, "assembly.stretch_tolerance", None),
        fit_mode=cfg_get(config, "assembly.fit_mode", None),
        force=True,
    )


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
    review.character_sheet(project, panel_ids, out_dir / "sheet_characters.png",
                           baseline=baseline)
    return issues


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("project_dir")
    ap.add_argument("--reletter", action="store_true",
                    help="re-run render_text + assemble_pages first (overwrites both)")
    ap.add_argument("--out", help="where to write the sheets (default: the project dir)")
    ap.add_argument("--baseline", help="another project dir to compare against")
    args = ap.parse_args(argv)

    project = load_project(args.project_dir)
    if args.reletter:
        reletter(project, load_config(REPO / "lazycomics_config.yaml"))
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
