"""Page assembly — panel PNGs + CBML layout → per-physical-page PNGs.

Reads the CBML to recover comic aspect + page geometry, lays out the
panels onto a per-page canvas with gutters, and writes one PNG per
physical page to ``<project>/pages/``.

Spreads (``PAGE spread:N``) are composited onto one ``N × page_w`` wide
canvas internally, then split at vertical midlines so the output is
always one PNG per physical page — the convention ``.cbz`` readers
expect.

Three-tier knob resolution (arg → ``assembly.<key>`` config → default):

* ``page_height_px`` — height of one physical page in pixels.
  Width is derived from ``comic.aspect``. Default 3000.
* ``gutter_px`` — gap between cells (and inside spans). Default 20.
* ``bg_color`` — gutter / canvas background. Default ``#ffffff``.
* ``stretch_tolerance`` — relative aspect drift allowed before
  switching from stretch to cover-crop. Default ``0.0`` (always
  cover-crop unless dims already match the slot exactly).

Panels are read from ``panels_text/<panel_id>.png``. A missing panel
yields a loud gray placeholder so the gap is obvious in review.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from lazycomics.config import cfg_get, load_config
from lazycomics.models import Project

__all__ = ["assemble_pages"]


_DEFAULT_PAGE_HEIGHT_PX = 3000
_DEFAULT_GUTTER_PX = 20
_DEFAULT_BG_COLOR = "#ffffff"
_DEFAULT_STRETCH_TOLERANCE = 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def assemble_pages(
    project: Project,
    *,
    page_height_px: int | None = None,
    gutter_px: int | None = None,
    bg_color: str | None = None,
    stretch_tolerance: float | None = None,
    force: bool = False,
) -> dict[int, Path]:
    """Composite panels into per-page PNGs under ``<project>/pages/``.

    Returns ``{physical_page_number: path}`` with 1-based page numbers
    matching the filename ``page_<NNN>.png``.

    Parameters are resolved arg → ``assembly.<key>`` config → default.
    Existing page files are skipped unless ``force=True`` (the user may
    have hand-touched a page).
    """
    cfg = load_config()
    page_h = _resolve(page_height_px, cfg, "assembly.page_height_px", _DEFAULT_PAGE_HEIGHT_PX)
    gutter = _resolve(gutter_px, cfg, "assembly.gutter_px", _DEFAULT_GUTTER_PX)
    bg = _resolve(bg_color, cfg, "assembly.bg_color", _DEFAULT_BG_COLOR)
    tol = _resolve(stretch_tolerance, cfg, "assembly.stretch_tolerance", _DEFAULT_STRETCH_TOLERANCE)

    from cbml_parser import CBMLParser  # lazy: matches enricher's pattern

    parser = CBMLParser()
    comic = parser.parse_file(str(project.cbml_path))
    return _assemble_from_comic(
        project, comic,
        page_height_px=page_h,
        gutter_px=gutter,
        bg_color=bg,
        stretch_tolerance=tol,
        force=force,
    )


def _resolve(arg_value: Any, cfg: Any, cfg_key: str, default: Any) -> Any:
    """arg → config → default. ``None`` means "fall through"."""
    if arg_value is not None:
        return arg_value
    return cfg_get(cfg, cfg_key, default)


# ---------------------------------------------------------------------------
# Core (testable without cbml_parser installed)
# ---------------------------------------------------------------------------


def _assemble_from_comic(
    project: Project,
    comic: Any,
    *,
    page_height_px: int = _DEFAULT_PAGE_HEIGHT_PX,
    gutter_px: int = _DEFAULT_GUTTER_PX,
    bg_color: str = _DEFAULT_BG_COLOR,
    stretch_tolerance: float = _DEFAULT_STRETCH_TOLERANCE,
    force: bool = False,
) -> dict[int, Path]:
    aspect_w, aspect_h = comic.aspect
    page_w = int(round(aspect_w * page_height_px / aspect_h))

    results: dict[int, Path] = {}
    for page in comic.pages:
        span = page.span
        first_page_num = page.index + 1  # 1-based
        first_path = project.pages_dir / _page_filename(first_page_num)
        if first_path.is_file() and not force:
            # Already rendered — collect any sibling pages for this spread.
            for i in range(span):
                pn = first_page_num + i
                p = project.pages_dir / _page_filename(pn)
                if p.is_file():
                    results[pn] = p
            continue

        canvas = _render_page_canvas(
            page, page_w, page_height_px, gutter_px, bg_color,
            stretch_tolerance, project,
        )

        # Split spread canvas into physical pages and write each.
        for i, physical in enumerate(_split_canvas(canvas, span, page_w)):
            pn = first_page_num + i
            out_path = project.pages_dir / _page_filename(pn)
            physical.save(out_path)
            results[pn] = out_path

    return results


def _render_page_canvas(
    page: Any,
    page_w: int,
    page_h: int,
    gutter: int,
    bg_color: str,
    stretch_tolerance: float,
    project: Project,
) -> Image.Image:
    """Render one page (or spread) onto a single (span*page_w × page_h) canvas."""
    span = page.span
    canvas_w = page_w * span
    canvas_h = page_h
    canvas = Image.new("RGB", (canvas_w, canvas_h), bg_color)

    max_col, max_row = _grid_dims(page)

    # Cell size — gutters are between cells only, no outer margin.
    cell_w = (canvas_w - gutter * (max_col - 1)) / max_col
    cell_h = (canvas_h - gutter * (max_row - 1)) / max_row

    for panel_idx, parser_panel in enumerate(page.panels):
        slot = parser_panel.slot
        c1, c2 = slot.cols
        r1, r2 = slot.rows
        slot_cols = c2 - c1 + 1
        slot_rows = r2 - r1 + 1

        slot_w = int(round(slot_cols * cell_w + (slot_cols - 1) * gutter))
        slot_h = int(round(slot_rows * cell_h + (slot_rows - 1) * gutter))
        slot_x = int(round((c1 - 1) * (cell_w + gutter)))
        slot_y = int(round((r1 - 1) * (cell_h + gutter)))

        panel_id = f"page_{page.index + 1}_panel_{panel_idx + 1}"
        panel_img = _load_panel_image(project, panel_id, slot_w, slot_h)
        fitted = _fit_to_slot(panel_img, slot_w, slot_h, stretch_tolerance)
        canvas.paste(fitted, (slot_x, slot_y))

    return canvas


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


def _grid_dims(page: Any) -> tuple[int, int]:
    """``(max_col, max_row)`` from the slot endpoints — works for any layout.

    Duplicated from enricher rather than imported as a private cross-module
    helper. If a third consumer appears, lift to a shared utility module.
    """
    max_col = max_row = 1
    for p in page.panels:
        max_col = max(max_col, p.slot.cols[1])
        max_row = max(max_row, p.slot.rows[1])
    return max_col, max_row


def _split_canvas(canvas: Image.Image, span: int, page_w: int) -> list[Image.Image]:
    """Cut the spread canvas into ``span`` physical-page slices.

    The last slice absorbs any rounding leftovers so total widths add up
    to the canvas width exactly.
    """
    if span == 1:
        return [canvas]

    canvas_w, canvas_h = canvas.size
    pages: list[Image.Image] = []
    for i in range(span):
        x1 = i * page_w
        x2 = (i + 1) * page_w if i < span - 1 else canvas_w
        pages.append(canvas.crop((x1, 0, x2, canvas_h)))
    return pages


def _page_filename(physical_page_num: int) -> str:
    return f"page_{physical_page_num:03d}.png"


# ---------------------------------------------------------------------------
# Image fit (stretch within tolerance, cover-crop beyond it)
# ---------------------------------------------------------------------------


def _fit_to_slot(
    img: Image.Image,
    slot_w: int,
    slot_h: int,
    stretch_tolerance: float,
) -> Image.Image:
    """Fit ``img`` into a (slot_w × slot_h) box.

    - If dims already match the slot exactly: return as-is.
    - If relative aspect drift ≤ ``stretch_tolerance``: stretch (resize).
    - Otherwise: cover-crop — scale so the image covers the slot, then
      centre-crop to slot dims. Preserves aspect; loses a sliver of edge
      content rather than distorting faces.
    """
    img_w, img_h = img.size
    if img_w == slot_w and img_h == slot_h:
        return img

    img_aspect = img_w / img_h
    slot_aspect = slot_w / slot_h
    drift = abs(img_aspect - slot_aspect) / slot_aspect

    if drift <= stretch_tolerance:
        return img.resize((slot_w, slot_h), Image.LANCZOS)

    # Cover-crop.
    scale = max(slot_w / img_w, slot_h / img_h)
    new_w = max(slot_w, int(round(img_w * scale)))
    new_h = max(slot_h, int(round(img_h * scale)))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - slot_w) // 2
    top = (new_h - slot_h) // 2
    return resized.crop((left, top, left + slot_w, top + slot_h))


# ---------------------------------------------------------------------------
# Panel sourcing
# ---------------------------------------------------------------------------


def _load_panel_image(project: Project, panel_id: str,
                      slot_w: int, slot_h: int) -> Image.Image:
    """Open the panel from ``panels_text/`` or return a labelled placeholder."""
    path = project.panels_text_dir / f"{panel_id}.png"
    if path.is_file():
        try:
            return Image.open(path).convert("RGB")
        except Exception as e:
            print(f"[assemble] {panel_id}: failed to open {path} ({e}); placeholder")
    else:
        print(f"[assemble] {panel_id}: missing source at {path}; placeholder")
    return _placeholder_panel(slot_w, slot_h, panel_id)


def _placeholder_panel(slot_w: int, slot_h: int, panel_id: str) -> Image.Image:
    """A loud gray rectangle labelled with the missing panel_id."""
    img = Image.new("RGB", (max(1, slot_w), max(1, slot_h)), (200, 200, 200))
    draw = ImageDraw.Draw(img)

    font = ImageFont.load_default()
    text = f"[missing: {panel_id}]"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = max(0, (slot_w - tw) // 2)
    y = max(0, (slot_h - th) // 2)
    draw.text((x, y), text, fill=(80, 80, 80), font=font)
    return img
