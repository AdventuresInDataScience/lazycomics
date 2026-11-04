#!/usr/bin/env python3
"""CLI: lettering bench — re-letter three sets of panels and tally the checks.

Usage::

    python tools/lettering_bench.py [--sets tune,holdout,synthetic]
                                    [--seeds 1,2,3] [--pages 12]

A lettering change is judged on more than the panels it was tuned on, so
that it generalises rather than fitting this one story's art:

* ``tune``: the panels lettering is developed against, re-lettered in
  place: the live e2e project (``tests/outputs/e2e``) and the A/B run
  ``w4_prompts``.
* ``holdout``: older runs with different art, checked only after a change
  is made, never while tuning it. Copies of three runs in
  ``tests/archive/`` (``e2e/2026-06-10``, ``ab_runs/2026-10-30_baseline``
  and ``ab_runs/2026-10-30_w3``) under
  ``tests/outputs/lettering_bench/holdout/<name>/``. The archives themselves
  are never written. The June run's panels were generated at the wrong
  aspect, so its ``aspect``/``slot`` issues are generation faults, not
  lettering ones.
* ``synthetic``: seeded random comics (:func:`synthetic_comic`) under
  ``tests/outputs/lettering_bench/synthetic/seed_<n>/``. They cover random page
  layouts, and through them panel aspects from strips to wide panels, casts
  of 1–4, 0–5 lines of 1–16 words within a word budget for the panel's
  share of the page (comic scripts run to about 35 words in a large panel;
  more can't be lettered without overprinting, which the ``overflow`` check
  reports), every bubble type, captions on any edge (often two on one), SFX
  anywhere, faces of any size and position, extras in the background, and
  speakers who aren't visible. The real pipeline
  letters them: ``enrich`` → ``render_text`` → ``assemble_pages``. Faces and
  who-is-who are given, not detected, so this set tests layout and tails,
  not the face detector or attribution.

Every set is re-lettered at the production page size (``assembly.*``
config), with persisted face matches cleared so attribution runs afresh.
Prints a table of issue counts by check kind per set, then attribution
against hand labels (``tests/fixtures/face_labels.json``, keyed by panel
image hash, so only images that were labelled are scored). Each project's
``sheet_pages.png`` is rewritten for eyeballing.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
# Allow running straight from a source checkout without installing the package.
sys.path.insert(0, str(REPO / "src"))

import lazycomics as lc  # noqa: E402
from lazycomics import review  # noqa: E402
from lazycomics.config import load_config  # noqa: E402
from lazycomics.geometry import resolution_for_aspect  # noqa: E402

from contact_sheet import reletter  # noqa: E402  (sibling script in tools/)

TESTS = REPO / "tests"
TUNE = [TESTS / "outputs" / "e2e", TESTS / "outputs" / "ab_runs" / "w4_prompts" / "project"]
HOLDOUT = {
    "2026-06-10_e2e": TESTS / "archive" / "e2e" / "2026-06-10",
    "2026-10-30_ab_baseline": TESTS / "archive" / "ab_runs" / "2026-10-30_baseline" / "project",
    "2026-10-30_ab_w3": TESTS / "archive" / "ab_runs" / "2026-10-30_w3" / "project",
}
LABELS = TESTS / "fixtures" / "face_labels.json"
OUT = TESTS / "outputs" / "lettering_bench"

# Issue kinds in table order; anything else is appended.
KINDS = ["overflow", "overlap", "face", "order", "stagger", "tail_size", "tail_cross",
         "tail_anchor", "no_tail", "slot", "aspect", "record", "missing"]
_FACE_KEYS = ("detected_faces", "actual_character_regions", "detected_faces_for")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sets", default="tune,holdout,synthetic")
    ap.add_argument("--seeds", default="1,2,3", help="synthetic comics to letter, one per seed")
    ap.add_argument("--pages", type=int, default=12, help="pages per synthetic comic")
    args = ap.parse_args(argv)
    config = load_config(REPO / "lazycomics_config.yaml")

    projects: dict[str, list[lc.Project]] = {}
    for name in args.sets.split(","):
        if name == "tune":
            projects[name] = [lc.load_project(p) for p in TUNE if (p / "manifest.json").is_file()]
            for p in projects[name]:
                _forget_faces(p)
        elif name == "holdout":
            projects[name] = [_holdout_copy(src, OUT / "holdout" / n)
                              for n, src in HOLDOUT.items() if src.is_dir()]
        elif name == "synthetic":
            projects[name] = [synthetic_comic(OUT / "synthetic" / f"seed_{s}", int(s), args.pages)
                              for s in args.seeds.split(",")]
        else:
            raise SystemExit(f"unknown set {name!r}")

    labels = json.loads(LABELS.read_text(encoding="utf-8")) if LABELS.is_file() else {}
    table: dict[str, collections.Counter] = {}
    for name, projs in projects.items():
        counts = table[name] = collections.Counter()
        for project in projs:
            reletter(project, config)
            pids = sorted((p.stem for p in project.enriched_dir.glob("*.json")),
                          key=review.panel_sort_key)
            issues = {pid: review.check_panel(project, pid) for pid in pids}
            review.pages_sheet(project, pids, project.base_dir / "sheet_pages.png", issues=issues)
            counts["panels"] += len(pids)
            counts["clean"] += sum(1 for v in issues.values() if not v)
            for found in issues.values():
                counts.update(i.kind for i in found)
            print(f"{name:9s} {project.base_dir.relative_to(REPO)}: "
                  f"{sum(1 for v in issues.values() if not v)}/{len(pids)} clean")
            for pid, found in issues.items():
                for i in found:
                    print(f"            {pid}: {i}")
            counts.update(_attribution(project, pids, labels))
    _print_table(table)
    return 0


# ---------------------------------------------------------------------------
# Sets
# ---------------------------------------------------------------------------


def _forget_faces(project: lc.Project) -> None:
    """Clear persisted faces and matches, so render_text detects and matches afresh."""
    for path in project.enriched_dir.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if any(k in data for k in _FACE_KEYS):
            for k in _FACE_KEYS:
                data.pop(k, None)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _holdout_copy(src: Path, dst: Path) -> lc.Project:
    """A fresh copy of an archived project, its absolute paths rewritten."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
        "pages_upscaled", "panels_upscaled", "pages_print", "output"))
    old, new = (json.dumps(str(p.resolve()))[1:-1] for p in (src, dst))
    for path in dst.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        if old in text:
            path.write_text(text.replace(old, new), encoding="utf-8")
    project = lc.load_project(dst)
    _forget_faces(project)
    return project


# ---------------------------------------------------------------------------
# Synthetic comics
# ---------------------------------------------------------------------------


_CAST = ["ANNA", "BEN", "CARA", "DEV"]
_WORDS = ("the a we you they it is was not never always maybe market numbers coffee night "
          "office late early really honestly what why how when where everyone nobody "
          "tomorrow again quietly strange normal tired fine absolutely perhaps").split()
_SFX = ["BANG", "THUD", "KRRK", "WHOOSH", "SKREEEEE", "RING RING", "SPLASH", "CLICK"]
_CAPTION_POS = ["top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right"]


def synthetic_comic(project_dir: Path, seed: int, pages: int) -> lc.Project:
    """Write, enrich and draw a seeded random comic; return its project.

    Each panel's image is a busy random backdrop with a cartoon head (plus
    shoulders) per visible character and up to two background extras. The
    faces and their owners are written into the enriched JSON as if
    ``render_text`` had detected and matched them, so they're used as given.
    """
    from cbml_parser.constants import PRESET_LAYOUTS

    rng = random.Random(seed)
    lines = [f"## Synthetic lettering bench, seed {seed}", "aspect: us-comic", ""]
    for _ in range(pages):
        preset = rng.choice(sorted(PRESET_LAYOUTS))
        lines += [f"PAGE preset:{preset}", ""]
        cols, rows = PRESET_LAYOUTS[preset]["grid"]
        for label, slot in PRESET_LAYOUTS[preset]["slots"].items():
            share = ((slot.cols[1] - slot.cols[0] + 1) * (slot.rows[1] - slot.rows[0] + 1)
                     / (cols * rows))
            budget = round(12 + 40 * share)  # words: 12 in a sliver, 52 on a splash
            cast = rng.sample(_CAST, rng.choices([1, 2, 3, 4], [4, 4, 2, 1])[0])
            lines += [f"PANEL {label}", "loc: ROOM", f"chars: {', '.join(cast)}", "> Something happens."]
            for pos in rng.sample(_CAPTION_POS, rng.choices([0, 1, 2], [6, 3, 2])[0]):
                lines.append(f"[caption bg:#141414 color:#ffffff pos:{pos}] {_sentence(rng, 2, 10)}")
            if rng.random() < 0.3:
                pos = rng.choice(_CAPTION_POS + ["center", "center"])
                lines.append(f"[sfx pos:{pos} color:#aa0000] {rng.choice(_SFX)}")
            speaker = None
            for _ in range(rng.choices(range(6), [2, 4, 4, 3, 2, 1])[0]):
                if budget < 1:
                    break
                # Exchanges alternate more often than not.
                others = [c for c in cast if c != speaker]
                speaker = rng.choice(others if others and rng.random() < 0.7 else cast)
                text = _sentence(rng, 1, min(16, budget))
                budget -= len(text.split())
                mark = rng.choices(['"', "~", "!", "?"], [6, 2, 2, 1])[0]
                lines.append({'"': f'{speaker}: "{text}"', "~": f"{speaker}: ~{text}~",
                              "!": f'{speaker}: !"{text.upper().rstrip(".?")}!"!',
                              "?": f'{speaker}: ?"{text}"?'}[mark])
            lines.append("")

    if project_dir.exists():
        shutil.rmtree(project_dir)
    project_dir.parent.mkdir(parents=True, exist_ok=True)
    cbml = project_dir.parent / f"{project_dir.name}.cbml"
    cbml.write_text("\n".join(lines), encoding="utf-8")
    project = lc.create_project(project_dir.name, cbml, base_dir=project_dir.parent)
    cbml.unlink()
    for panel in lc.enrich(project):
        _draw_panel(project, panel.panel_id, rng)
    return project


def _sentence(rng: random.Random, lo: int, hi: int) -> str:
    words = [rng.choice(_WORDS) for _ in range(rng.randint(lo, hi))]
    return " ".join(words).capitalize() + rng.choice([".", ".", "?", "..."])


def _draw_panel(project: lc.Project, panel_id: str, rng: random.Random) -> None:
    path = project.enriched_dir / f"{panel_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    W, H = resolution_for_aspect(float(data["aspect_ratio"]), 1024)
    img = Image.new("RGB", (W, H), tuple(rng.randint(40, 200) for _ in range(3)))
    draw = ImageDraw.Draw(img)
    for _ in range(rng.randint(10, 60)):  # clutter: busy in places, flat elsewhere
        x, y = rng.randrange(W), rng.randrange(H)
        w, h = rng.randint(10, W // 3), rng.randint(10, H // 3)
        draw.rectangle([x, y, x + w, y + h], outline=tuple(rng.randint(0, 255) for _ in range(3)),
                       width=rng.randint(1, 4))

    cast = [r["character"] for r in data.get("bubble_layout") or []]
    visible = [c for c in cast if rng.random() < 0.85]
    if rng.random() < 0.2:  # the generator drew them in another order
        rng.shuffle(visible)
    short = min(W, H)
    size_hi = 0.6 if len(cast) == 1 else 0.35
    faces, who = [], {}
    for i, c in enumerate(visible):
        fh = int(short * rng.uniform(0.08, size_hi))
        fw = int(fh * rng.uniform(0.7, 0.9))
        cx = int((i + 0.5) / len(visible) * W + rng.uniform(-0.1, 0.1) * W)
        cy = int(H * rng.uniform(0.2, 0.7))
        box = [max(0, cx - fw // 2), max(0, cy - fh // 2), min(W, cx + fw // 2), min(H, cy + fh // 2)]
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            continue
        _draw_head(draw, box, rng)
        faces.append(box)
        who[c] = box
    for _ in range(rng.choices([0, 1, 2], [5, 3, 2])[0]):  # background extras
        fh = int(short * rng.uniform(0.03, 0.07))
        x, y = rng.randrange(W - fh), rng.randrange(H - fh)
        box = [x, y, x + fh * 4 // 5, y + fh]
        _draw_head(draw, box, rng)
        faces.append(box)

    out = project.panels_dir / f"{panel_id}.png"
    img.save(out)
    data["detected_faces"] = faces
    data["actual_character_regions"] = who
    data["detected_faces_for"] = hashlib.sha1(out.read_bytes()).hexdigest()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _draw_head(draw: ImageDraw.ImageDraw, box: list[int], rng: random.Random) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    skin = tuple(rng.randint(120, 240) for _ in range(3))
    draw.rectangle([x0 - w // 2, y1, x1 + w // 2, y1 + 2 * h], fill=tuple(rng.randint(0, 120) for _ in range(3)))
    draw.ellipse([x0 - w // 10, y0 - h // 4, x1 + w // 10, y0 + h // 2], fill=tuple(rng.randint(0, 120) for _ in range(3)))
    draw.ellipse(box, fill=skin, outline=(0, 0, 0), width=max(1, w // 30))
    for ex in (x0 + w // 3, x0 + 2 * w // 3):
        draw.ellipse([ex - w // 14, y0 + h * 2 // 5, ex + w // 14, y0 + h // 2], fill=(0, 0, 0))
    draw.line([x0 + w // 3, y0 + h * 3 // 4, x0 + 2 * w // 3, y0 + h * 3 // 4], fill=(0, 0, 0), width=max(1, w // 25))


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _attribution(project: lc.Project, pids: list[str], labels: dict) -> collections.Counter:
    """Score the matches render_text made against hand labels, where they exist.

    A labelled character is right when its matched face overlaps the
    labelled one (IoU > 0.5), wrong when it's matched elsewhere, and
    "unplaced" when it's visible but got no match (no tail: fail closed).
    A character with no label isn't visible; matching it to any face is wrong.
    """
    score: collections.Counter = collections.Counter()
    for pid in pids:
        png = project.panels_dir / f"{pid}.png"
        label = labels.get(hashlib.sha1(png.read_bytes()).hexdigest()) if png.is_file() else None
        if label is None:
            continue
        data = json.loads((project.enriched_dir / f"{pid}.json").read_text(encoding="utf-8"))
        matched = data.get("actual_character_regions") or {}
        speakers = {line["character"] for line in data.get("dialogue_lines") or []}
        for c in {r["character"] for r in data.get("bubble_layout") or []}:
            want, got = label["who"].get(c), matched.get(c)
            verdict = ("right" if want and got and _iou(want, got) > 0.5
                       else "unplaced" if want and not got
                       else "ok" if not want and not got else "wrong")
            score[f"attr {verdict}"] += 1
            if c in speakers:
                score[f"attr speakers {verdict}"] += 1
    return score


def _iou(a: list[int], b: list[int]) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def _print_table(table: dict[str, collections.Counter]) -> None:
    sets = list(table)
    rows = ["panels", "clean"] + KINDS + sorted(
        {k for c in table.values() for k in c} - set(KINDS) - {"panels", "clean"})
    print("\n" + "".join(f"{'':22s}" if i == 0 else "" for i in range(1))
          + "".join(f"{s:>11s}" for s in sets))
    for r in rows:
        if any(table[s][r] for s in sets) or r in ("panels", "clean"):
            print(f"{r:22s}" + "".join(f"{table[s][r]:>11d}" for s in sets))


if __name__ == "__main__":
    raise SystemExit(main())
