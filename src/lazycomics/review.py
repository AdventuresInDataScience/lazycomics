"""Golden-subset review: machine checks + contact sheets (dev utility).

Reads what the pipeline already wrote — ``panels/``, ``panels_text/``,
``enriched/``, ``pages/``, the renderer's ``panels_text/<id>.layout.json``
and the assembler's ``panel_slots.json`` — and turns it into (a) a list of
issues per panel and (b) two contact-sheet PNGs for eyeballing:

* **pages sheet** — per page, one row per panel: raw | lettered | as placed
  on the page (the slot-cropped part), plus the assembled page alongside, so
  cropping damage is visible at a glance;
* **characters sheet** — per character, the reference beside a head crop
  from every appearance (optionally over a baseline run's crops);
* **dataset sheet** — a grid of named thumbnails, for hand-picking a LoRA
  training set.

``tools/golden_run.py``, ``tools/contact_sheet.py`` and
``tools/train_lora.py`` drive it; nothing here touches a GPU.

Checks (``check_panel``), by ``Issue.kind``:

* ``aspect`` — generated panel vs its slot; >5% off means it gets cropped.
* ``slot`` — a bubble / caption / SFX box falls outside the part of the panel
  its page slot shows (i.e. it gets cropped off the page).
* ``order`` — consecutive CBML lines whose bubbles read backwards in Western
  Z-order (next line clearly higher, or same row but to the left).
* ``overlap`` / ``face`` — boxes colliding; a bubble covering more than a
  quarter of a detected face.
* ``no_tail`` — a speech/thought bubble drawn without a tail because the
  speaker's face wasn't found.
* ``tail_anchor`` — a tail aimed at a point inside no detected face. True by
  construction today (tails only aim at face centres); a regression guard.
* ``missing`` / ``record`` — inputs absent (no panel, no layout/slot record).

Tail *correctness* (pointing at the right character) can't be checked
without knowing who is who; that's what the sheets are for.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any, NamedTuple

from PIL import Image, ImageDraw, ImageFont

from lazycomics import face_detector
from lazycomics.models import Project

__all__ = ["Issue", "check_panel", "pages_sheet", "character_sheet", "dataset_sheet",
           "panel_sort_key"]


_ASPECT_TOL = 0.05
_FACE_COVER_FRAC = 0.25
# Bounding rects of rounded bubbles can intersect at the corners without the
# drawn shapes touching; ignore overlaps below this fraction of the smaller box.
_OVERLAP_MIN_FRAC = 0.01
# Character-sheet head crops only trust a face this confident. The hybrid
# detector accepts from 0.5, and on stylised panels its 0.5-0.6 hits are often
# not faces at all (page_6_panel_1: a 0.53 hit on the floor, real face missed).
_HEAD_CROP_MIN_SCORE = 0.6
_BG = (255, 255, 255)
_GAP = 12


class Issue(NamedTuple):
    kind: str
    message: str

    def __str__(self) -> str:
        return self.message


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_panel(project: Project, panel_id: str) -> list[Issue]:
    """Return the issues found for one panel (empty list = clean)."""
    panel_png = project.panels_dir / f"{panel_id}.png"
    if not panel_png.is_file():
        return [Issue("missing", "no generated panel")]

    issues: list[Issue] = []
    enriched = _read_json(project.enriched_dir / f"{panel_id}.json") or {}
    with Image.open(panel_png) as im:
        w, h = im.size
    want = float(enriched.get("aspect_ratio") or 1.0)
    got = w / h
    if abs(got - want) / want > _ASPECT_TOL:
        issues.append(Issue(
            "aspect", f"aspect {got:.2f} ({w}x{h}) vs slot {want:.2f}: the assembler will crop it",
        ))

    layout = _read_json(project.panels_text_dir / f"{panel_id}.layout.json")
    if layout is None:
        issues.append(Issue("record", "no layout record (re-run render_text)"))
        return issues

    issues += _slot_issues(project, panel_id, layout)
    bubbles = layout.get("bubbles") or []
    issues += _reading_order_issues(bubbles)
    issues += _overlap_issues(layout)
    issues += _tail_issues(layout)
    return issues


def _boxes(layout: dict[str, Any]) -> list[tuple[str, list[int]]]:
    """Every overlay box on the panel, named for messages."""
    boxes = [(f"line {b['line'] + 1}", b["rect"]) for b in layout.get("bubbles") or []]
    boxes += [(f"caption {i + 1}", r) for i, r in enumerate(layout.get("captions") or [])]
    boxes += [(f"sfx {i + 1}", r) for i, r in enumerate(layout.get("sfx") or [])]
    return boxes


def _slot_issues(project: Project, panel_id: str, layout: dict[str, Any]) -> list[Issue]:
    """Overlay boxes that fall outside the part of the panel its slot shows."""
    entry = (_read_json(project.base_dir / "panel_slots.json") or {}).get(panel_id)
    if entry is None:
        return [Issue("record", "no page-slot record (run assemble_pages)")]
    vx0, vy0, vx1, vy1 = entry["visible"]
    issues = []
    for name, (x0, y0, x1, y1) in _boxes(layout):
        # 1px slack for the window's rounding.
        if x0 < vx0 - 1 or y0 < vy0 - 1 or x1 > vx1 + 1 or y1 > vy1 + 1:
            issues.append(Issue(
                "slot", f"{name} is cut off by its page slot (slot shows x {vx0}-{vx1}, y {vy0}-{vy1})",
            ))
    return issues


def _reading_order_issues(bubbles: list[dict[str, Any]]) -> list[Issue]:
    """Consecutive CBML lines whose bubbles read backwards.

    Line B correctly follows line A when B starts in a lower row, or in the
    same row (tops within half the smaller bubble's height) to A's right.
    """
    issues: list[Issue] = []
    ordered = sorted(bubbles, key=lambda b: b["line"])
    for a, b in zip(ordered, ordered[1:]):
        ax0, ay0, ax1, ay1 = a["rect"]
        bx0, by0, bx1, by1 = b["rect"]
        band = min(ay1 - ay0, by1 - by0) / 2
        la, lb = a["line"] + 1, b["line"] + 1
        if by0 < ay0 - band:
            issues.append(Issue("order", f"reading order: line {lb} sits above line {la}"))
        elif abs(by0 - ay0) <= band and (bx0 + bx1) < (ax0 + ax1):
            issues.append(Issue("order", f"reading order: line {lb} sits left of line {la} in the same row"))
    return issues


def _overlap_issues(layout: dict[str, Any]) -> list[Issue]:
    boxes = _boxes(layout)
    issues: list[Issue] = []
    for i, (name_a, ra) in enumerate(boxes):
        for name_b, rb in boxes[i + 1:]:
            smaller = max(1, min(_area(ra), _area(rb)))
            if _overlap_area(ra, rb) / smaller > _OVERLAP_MIN_FRAC:
                issues.append(Issue("overlap", f"overlap: {name_a} and {name_b}"))

    faces = layout.get("faces") or []
    for b in layout.get("bubbles") or []:
        for f in faces:
            if _overlap_area(b["rect"], f) / max(1, _area(f)) > _FACE_COVER_FRAC:
                issues.append(Issue("face", f"line {b['line'] + 1} covers a face"))
                break
    return issues


def _tail_issues(layout: dict[str, Any]) -> list[Issue]:
    faces = layout.get("faces") or []
    issues: list[Issue] = []
    for b in layout.get("bubbles") or []:
        line = b["line"] + 1
        if b["type"] in ("speech", "thought") and not b["tail"]:
            issues.append(Issue("no_tail", f"line {line} ({b['speaker']}): no tail, speaker's face not found"))
        if b["tail"]:
            ax, ay = b["anchor"]
            if not any(f[0] <= ax <= f[2] and f[1] <= ay <= f[3] for f in faces):
                issues.append(Issue("tail_anchor", f"line {line}: tail aims at no detected face"))
    return issues


def _area(r: list[int]) -> int:
    return max(0, r[2] - r[0]) * max(0, r[3] - r[1])


def _overlap_area(a: list[int], b: list[int]) -> int:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy


# ---------------------------------------------------------------------------
# Contact sheets
# ---------------------------------------------------------------------------


def pages_sheet(
    project: Project,
    panel_ids: list[str],
    out_path: Path,
    *,
    baseline: Project | None = None,
    issues: dict[str, list[Issue]] | None = None,
) -> Path:
    """Per page: one row per panel (raw | lettered | as placed on the page)
    with its issues, and the assembled page(s) alongside.

    "As placed" is the lettered panel cropped to the window its slot shows
    (``panel_slots.json``), so cropping damage is visible at a glance. The
    optional baseline column (another run's ``panels_text/``) is for A/B
    review of an experiment.
    """
    issues = issues or {}
    slots = _read_json(project.base_dir / "panel_slots.json") or {}
    by_page: dict[int, list[str]] = {}
    for pid in sorted(panel_ids, key=panel_sort_key):
        by_page.setdefault(panel_sort_key(pid)[0], []).append(pid)

    blocks: list[Image.Image] = []
    for page_num, pids in by_page.items():
        rows = []
        for pid in pids:
            enriched = _read_json(project.enriched_dir / f"{pid}.json") or {}
            aspect = float(enriched.get("aspect_ratio") or 1.0)
            # Narrow panels get taller tiles so their text stays readable.
            h = max(240, min(520, round(200 / aspect)))
            lettered = project.panels_text_dir / f"{pid}.png"
            tiles = [
                _tile(project.panels_dir / f"{pid}.png", h, "raw"),
                _tile(lettered, h, "lettered"),
                _placed_tile(lettered, slots.get(pid), h),
            ]
            if baseline is not None:
                tiles.append(_tile(baseline.panels_text_dir / f"{pid}.png", h, "baseline"))
            chars = ", ".join(c["identifier"] for c in enriched.get("characters") or [])
            header = f"{pid}   slot {aspect:.2f}   {chars}"
            rows.append(_row(header, tiles, [str(i) for i in issues.get(pid, [])]))

        left = _stack(rows, gap=_GAP)
        page_nums = next((slots[p]["pages"] for p in pids if p in slots), [page_num])
        page_img = _assembled(project, page_nums, left.height - 22)
        label = (f"assembled page {page_nums[0]}" if len(page_nums) == 1
                 else f"assembled pages {page_nums[0]}-{page_nums[-1]} (spread)")
        right = _label_tile(page_img, label)
        block = Image.new("RGB", (left.width + 2 * _GAP + right.width,
                                  max(left.height, right.height)), _BG)
        block.paste(left, (0, 0))
        block.paste(right, (left.width + 2 * _GAP, 0))
        blocks.append(block)

    return _save(_stack(blocks, gap=_GAP * 4), out_path)


def character_sheet(
    project: Project,
    panel_ids: list[str],
    out_path: Path,
    *,
    baseline: Project | None = None,
    tile_h: int = 240,
) -> Path:
    """One row per character: their reference, then a head crop from every
    appearance — the W4 consistency yardstick.

    Each crop centres on the face ``render_text`` matched to that character
    (``actual_character_regions``, trusted only while ``detected_faces_for``
    still matches the panel image). A solo panel with no match falls back to
    the largest confidently-detected face. Otherwise the whole panel is shown,
    labelled "not located". With ``baseline`` (another run of the same
    panels), each crop has that run's crop beneath it for A/B review.
    """
    appearances: dict[str, list[str]] = {}
    refs: dict[str, str | None] = {}
    for pid in sorted(panel_ids, key=panel_sort_key):
        chars = (_read_json(project.enriched_dir / f"{pid}.json") or {}).get("characters") or []
        for c in chars:
            appearances.setdefault(c["identifier"], []).append(pid)
            refs.setdefault(c["identifier"], (c.get("reference_images") or [None])[0])

    rows: list[Image.Image] = []
    for name in sorted(appearances, key=lambda n: -len(appearances[n])):
        tiles = [_tile(Path(refs[name]) if refs[name] else None, tile_h, "reference")]
        for pid in appearances[name]:
            tile = _appearance_tile(project, pid, name, tile_h)
            if baseline is not None:
                tile = _stack([tile, _appearance_tile(baseline, pid, name, tile_h, "baseline ")],
                              gap=4)
            tiles.append(tile)
        header = f"{name}   {len(appearances[name])} appearance(s)"
        if baseline is not None:
            header += "   (top: this run, bottom: baseline)"
        rows.append(_row(header, tiles, None))
    return _save(_stack(rows, gap=_GAP * 2), out_path)


def dataset_sheet(
    images: list[Path],
    out_path: Path,
    *,
    title: str = "",
    per_row: int = 7,
    tile_h: int = 256,
) -> Path:
    """Every image as a thumbnail labelled with its file stem, ``per_row``
    to a row, under ``title``: a LoRA training set to hand-pick from."""
    rows = [
        _row(title if i == 0 else "", [_tile(p, tile_h, p.stem) for p in images[i:i + per_row]], None)
        for i in range(0, len(images), per_row)
    ]
    return _save(_stack(rows or [_row(title, [_tile(None, tile_h, "no images")], None)], gap=_GAP),
                 out_path)


def _appearance_tile(project: Project, pid: str, name: str, h: int, prefix: str = "") -> Image.Image:
    """Head crop of character ``name`` in panel ``pid`` (see ``character_sheet``)."""
    enriched = _read_json(project.enriched_dir / f"{pid}.json") or {}
    cast = len(enriched.get("characters") or [])
    label = prefix + (pid if cast == 1 else f"{pid} ({cast} chars)")
    panel_png = project.panels_dir / f"{pid}.png"
    if not panel_png.is_file():
        return _tile(None, h, label)
    data = panel_png.read_bytes()
    with Image.open(io.BytesIO(data)) as im:
        img = im.convert("RGB")
    regions = {}
    if enriched.get("detected_faces_for") == hashlib.sha1(data).hexdigest():
        regions = enriched.get("actual_character_regions") or {}
    faces = [regions[name]] if name in regions else (_confident_faces(img) if cast == 1 else [])
    if faces:
        img = _head_crop(img, faces)
    else:
        label += " - not located"
    return _label_tile(_fit_h(img, h), label)


def _confident_faces(img: Image.Image) -> list[list[int]]:
    """Face boxes scoring at least ``_HEAD_CROP_MIN_SCORE`` (module-level seam)."""
    return [[f.x0, f.y0, f.x1, f.y1] for f in face_detector.detect_faces(img)
            if f.score >= _HEAD_CROP_MIN_SCORE]


def _head_crop(img: Image.Image, faces: list[list[int]]) -> Image.Image:
    """Head-and-shoulders crop around the largest face; whole image if none."""
    if not faces:
        return img
    x0, y0, x1, y1 = max(faces, key=lambda f: (f[2] - f[0]) * (f[3] - f[1]))
    s = max(x1 - x0, y1 - y0)
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    box = (
        max(0, cx - int(1.5 * s)), max(0, cy - int(1.2 * s)),
        min(img.width, cx + int(1.5 * s)), min(img.height, cy + int(2.0 * s)),
    )
    return img.crop(box)


def _placed_tile(lettered: Path, slot: dict[str, Any] | None, h: int) -> Image.Image:
    """The lettered panel cropped to what its page slot shows."""
    if slot is None or not lettered.is_file():
        return _tile(None, h, "on page (not assembled)")
    with Image.open(lettered) as im:
        shown = _area(slot["visible"]) / max(1, im.width * im.height)
        img = im.convert("RGB").crop(tuple(slot["visible"]))
    # Gutter / grid rounding trims a pixel or two off most panels; give the
    # fraction so a real crop (the pre-W1 33%) stands out from noise.
    label = "on page" if shown >= 1.0 else f"on page ({shown:.1%} shown)"
    return _label_tile(_fit_h(img, h), label)


def _assembled(project: Project, page_nums: list[int], h: int) -> Image.Image:
    """Physical page(s) side by side (a spread reads as one), scaled to ``h``."""
    imgs = []
    for n in page_nums:
        p = project.pages_dir / f"page_{n:03d}.png"
        if p.is_file():
            with Image.open(p) as im:
                imgs.append(_fit_h(im.convert("RGB"), h))
    if not imgs:
        return Image.new("RGB", (max(120, h * 2 // 3), h), (200, 200, 200))
    out = Image.new("RGB", (sum(i.width for i in imgs), h), _BG)
    x = 0
    for i in imgs:
        out.paste(i, (x, 0))
        x += i.width
    return out


def panel_sort_key(panel_id: str) -> tuple[int, int]:
    """``page_10_panel_2`` -> ``(10, 2)``, so page 10 sorts after page 2."""
    parts = panel_id.split("_")
    return int(parts[1]), int(parts[3])


# ---------------------------------------------------------------------------
# Image plumbing
# ---------------------------------------------------------------------------


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _fit_h(img: Image.Image, h: int) -> Image.Image:
    return img.resize((max(1, round(img.width * h / img.height)), h), Image.LANCZOS)


def _tile(path: Path | None, h: int, label: str) -> Image.Image:
    """A labelled thumbnail of ``path``, or a grey 'missing' placeholder."""
    if path is not None and path.is_file():
        with Image.open(path) as im:
            img = _fit_h(im.convert("RGB"), h)
    else:
        img = Image.new("RGB", (max(120, h * 2 // 3), h), (200, 200, 200))
        ImageDraw.Draw(img).text((8, h // 2), "missing", fill=(90, 90, 90), font=_font(16))
    return _label_tile(img, label)


def _label_tile(img: Image.Image, label: str) -> Image.Image:
    """``img`` with ``label`` above it, widened if the label is wider."""
    font = _font(15)
    label_w = int(ImageDraw.Draw(img).textlength(label, font=font)) + 4
    out = Image.new("RGB", (max(img.width, label_w), img.height + 22), _BG)
    out.paste(img, (0, 22))
    ImageDraw.Draw(out).text((2, 2), label, fill=(60, 60, 60), font=font)
    return out


def _row(header: str, tiles: list[Image.Image], issues: list[str] | None) -> Image.Image:
    """Header line, tiles side by side, then one red line per issue.

    ``issues=None`` omits the issue area; ``[]`` prints a green "no issues".
    """
    line_h = 22
    tiles_w = sum(t.width for t in tiles) + _GAP * (len(tiles) - 1)
    tiles_h = max(t.height for t in tiles)
    lines = [] if issues is None else (issues or ["no issues"])
    width = max(tiles_w, 900)
    out = Image.new("RGB", (width, line_h + tiles_h + line_h * len(lines) + 6), _BG)
    draw = ImageDraw.Draw(out)
    draw.text((0, 0), header, fill=(0, 0, 0), font=_font(18))
    x = 0
    for t in tiles:
        out.paste(t, (x, line_h))
        x += t.width + _GAP
    colour = (190, 30, 30) if issues else (30, 130, 60)
    for i, text in enumerate(lines):
        draw.text((0, line_h + tiles_h + 4 + i * line_h), text, fill=colour, font=_font(16))
    return out


def _stack(rows: list[Image.Image], *, gap: int) -> Image.Image:
    width = max((r.width for r in rows), default=1)
    height = sum(r.height for r in rows) + gap * max(0, len(rows) - 1)
    out = Image.new("RGB", (width, max(1, height)), _BG)
    y = 0
    for r in rows:
        out.paste(r, (0, y))
        y += r.height + gap
    return out


def _save(img: Image.Image, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
