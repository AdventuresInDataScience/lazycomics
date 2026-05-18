"""Text rendering — panel PNG + enriched JSON → panel PNG with overlays.

Three overlay kinds per panel:

* **Caption boxes** (CBML ``[caption ...]``): filled rectangles at one
  of six corner anchors, with bg/text colours from the CBML.
* **SFX** (CBML ``[sfx ...]``): styled free-floating text at one of
  seven anchors (six corners + ``center``). No background box; rendered
  with a contrasting outline for legibility on any background.
* **Dialogue bubbles** (CBML ``NAME: "text"``): rounded rectangles with
  text inside. Auto-placed in the half of the panel that doesn't contain
  faces (MediaPipe), so a closeup panel doesn't end up with a bubble
  over the subject's face.

Bubble type currently varies only border weight (speech 2, shout 4,
whisper 1, thought 2) and is identical otherwise. Speaker-pointing tails
and per-type shapes (jagged shout, cloudy thought) are deferred — they
need character-to-face matching and more complex geometry.

The output PNG may be reviewed and hand-overwritten; the renderer skips
panels whose output already exists unless ``force=True``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import mediapipe as mp
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from lazycomics.models import Project

__all__ = ["render_text"]


# Common system font locations in priority order. First hit wins.
_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "C:\\Windows\\Fonts\\arialbd.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_text(
    project: Project,
    panels: list[str] | None = None,
    *,
    force: bool = False,
) -> dict[str, Path]:
    """Overlay text/bubbles/SFX onto each panel image.

    Reads ``<project>/panels/<panel_id>.png`` plus the matching enriched
    JSON; writes ``<project>/panels_text/<panel_id>.png``. Returns
    ``{panel_id: output_path}``.

    Panels whose output already exists are skipped (the user may have
    hand-edited them) unless ``force=True``.
    """
    if panels is None:
        panel_ids = sorted(p.stem for p in project.panels_dir.glob("*.png"))
    else:
        panel_ids = list(panels)

    results: dict[str, Path] = {}
    for panel_id in panel_ids:
        out_path = project.panels_text_dir / f"{panel_id}.png"
        if out_path.is_file() and not force:
            results[panel_id] = out_path
            continue

        panel_path = project.panels_dir / f"{panel_id}.png"
        if not panel_path.is_file():
            print(f"[text] skipping {panel_id}: no source panel at {panel_path}")
            continue

        enriched_path = project.enriched_dir / f"{panel_id}.json"
        if not enriched_path.is_file():
            print(f"[text] skipping {panel_id}: no enriched JSON")
            continue

        panel_data = json.loads(enriched_path.read_text(encoding="utf-8"))
        img = Image.open(panel_path).convert("RGBA")
        img = _render_one(img, panel_data)
        img.convert("RGB").save(out_path)
        results[panel_id] = out_path

    return results


# ---------------------------------------------------------------------------
# Per-panel composition (order: captions, SFX, dialogue on top)
# ---------------------------------------------------------------------------


def _render_one(img: Image.Image, panel: dict[str, Any]) -> Image.Image:
    img = _render_captions(img, panel.get("caption_boxes") or [])
    img = _render_sfx(img, panel.get("sfx_lines") or [])
    img = _render_dialogue(img, panel.get("dialogue_lines") or [])
    return img


def _render_captions(img: Image.Image, captions: list[dict[str, Any]]) -> Image.Image:
    if not captions:
        return img
    draw = ImageDraw.Draw(img)
    W, H = img.size
    font = _load_font(size=max(14, W // 40))
    margin = 20
    padding = 10
    max_text_width = max(20, int(W * 0.4) - 2 * padding)

    for cap in captions:
        text = (cap.get("text") or "").strip()
        if not text:
            continue
        bg = cap.get("bg_color") or "#000000"
        tc = cap.get("text_color") or "#ffffff"
        pos = cap.get("position") or "top-left"

        lines = _wrap_text(text, font, max_text_width, draw)
        line_sizes = [_measure(draw, line, font) for line in lines]
        text_w = max(w for w, _ in line_sizes)
        text_h = sum(h for _, h in line_sizes)

        box_w = text_w + 2 * padding
        box_h = text_h + 2 * padding
        x, y = _anchor_to_xy(pos, W, H, box_w, box_h, margin)

        draw.rectangle([x, y, x + box_w, y + box_h], fill=bg)

        y_cursor = y + padding
        for line, (_, lh) in zip(lines, line_sizes):
            draw.text((x + padding, y_cursor), line, fill=tc, font=font)
            y_cursor += lh
    return img


def _render_sfx(img: Image.Image, sfx_lines: list[dict[str, Any]]) -> Image.Image:
    if not sfx_lines:
        return img
    draw = ImageDraw.Draw(img)
    W, H = img.size
    font = _load_font(size=max(28, W // 18))
    margin = 20

    for sfx in sfx_lines:
        text = (sfx.get("text") or "").strip()
        if not text:
            continue
        color = sfx.get("color") or "#000000"
        pos = sfx.get("position") or "center"

        text_w, text_h = _measure(draw, text, font)
        x, y = _anchor_to_xy(pos, W, H, text_w, text_h, margin, allow_center=True)

        # Outline for legibility against any background.
        outline = "#ffffff" if _is_dark(color) else "#000000"
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                if dx == 0 and dy == 0:
                    continue
                if dx * dx + dy * dy <= 4:
                    draw.text((x + dx, y + dy), text, fill=outline, font=font)

        draw.text((x, y), text, fill=color, font=font)
    return img


def _render_dialogue(img: Image.Image, dialogue: list[dict[str, Any]]) -> Image.Image:
    if not dialogue:
        return img
    draw = ImageDraw.Draw(img)
    W, H = img.size
    font = _load_font(size=max(14, W // 40))
    margin = 20
    padding = 12
    max_bubble_width = max(40, int(W * 0.45))

    safe_top = _bubbles_should_go_top(img)
    bubble_x = margin
    y_cursor = margin if safe_top else H - margin

    for line in dialogue:
        text = (line.get("text") or "").strip()
        if not text:
            continue
        bubble_type = line.get("bubble_type") or "speech"

        lines_wrapped = _wrap_text(text, font, max_bubble_width - 2 * padding, draw)
        line_sizes = [_measure(draw, l, font) for l in lines_wrapped]
        text_w = max(w for w, _ in line_sizes)
        text_h = sum(h for _, h in line_sizes)

        bubble_w = text_w + 2 * padding
        bubble_h = text_h + 2 * padding

        if safe_top:
            bubble_y = y_cursor
            y_cursor += bubble_h + 10
        else:
            bubble_y = y_cursor - bubble_h
            y_cursor -= bubble_h + 10

        _draw_bubble(draw, bubble_x, bubble_y, bubble_w, bubble_h, bubble_type)

        y_text = bubble_y + padding
        for l, (_, lh) in zip(lines_wrapped, line_sizes):
            draw.text((bubble_x + padding, y_text), l, fill="#000000", font=font)
            y_text += lh
    return img


# ---------------------------------------------------------------------------
# Drawing primitives
# ---------------------------------------------------------------------------


_BUBBLE_BORDER_WIDTH = {"speech": 2, "shout": 4, "whisper": 1, "thought": 2}


def _draw_bubble(draw: ImageDraw.ImageDraw, x: int, y: int,
                 w: int, h: int, bubble_type: str) -> None:
    """Rounded rectangle, border width varies by bubble type."""
    radius = min(20, h // 2)
    border = _BUBBLE_BORDER_WIDTH.get(bubble_type, 2)
    draw.rounded_rectangle(
        [x, y, x + w, y + h],
        radius=radius,
        fill="#ffffff",
        outline="#000000",
        width=border,
    )


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Best available system font at the requested size; bitmap fallback."""
    for path in _FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        # Pillow <10: load_default takes no args.
        return ImageFont.load_default()


def _measure(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap_text(text: str, font, max_width: int,
               draw: ImageDraw.ImageDraw) -> list[str]:
    """Word-wrap to fit ``max_width`` px. Returns at least one line."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = current + " " + word
        w, _ = _measure(draw, candidate, font)
        if w <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _anchor_to_xy(anchor: str, panel_w: int, panel_h: int,
                  box_w: int, box_h: int, margin: int,
                  allow_center: bool = False) -> tuple[int, int]:
    """Top-left ``(x, y)`` for a box placed at ``anchor`` within the panel.

    Caption anchors: top-{left,center,right}, bottom-{left,center,right}.
    SFX adds ``center`` (full centring) when ``allow_center=True``.
    """
    # Vertical
    if anchor == "center" and allow_center:
        y = (panel_h - box_h) // 2
    elif anchor.startswith("top-"):
        y = margin
    elif anchor.startswith("bottom-"):
        y = panel_h - box_h - margin
    else:
        y = margin

    # Horizontal
    if anchor == "center" and allow_center:
        x = (panel_w - box_w) // 2
    elif anchor.endswith("-left"):
        x = margin
    elif anchor.endswith("-right"):
        x = panel_w - box_w - margin
    elif anchor.endswith("-center"):
        x = (panel_w - box_w) // 2
    else:
        x = margin

    return x, y


def _is_dark(hex_color: str) -> bool:
    """Crude luminance test (ITU-R BT.601 coefficients)."""
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (0.299 * r + 0.587 * g + 0.114 * b) < 128
    except (ValueError, IndexError):
        return True


# ---------------------------------------------------------------------------
# Face-aware bubble placement
# ---------------------------------------------------------------------------


def _bubbles_should_go_top(img: Image.Image) -> bool:
    """Return ``True`` if bubbles should stack from the top of the panel.

    Uses MediaPipe to find faces. If faces are mostly in the bottom half
    of the panel, bubbles go up top (to avoid them); otherwise they go
    bottom. No faces / detection error → defaults to top.

    Module-level so tests can monkeypatch.
    """
    try:
        detector = mp.solutions.face_detection.FaceDetection(min_detection_confidence=0.5)
        arr = np.array(img.convert("RGB"))
        results = detector.process(arr)
    except Exception as e:
        print(f"[text] face detection error during bubble placement: {e}")
        return True

    if not results.detections:
        return True

    centres = []
    for det in results.detections:
        bbox = det.location_data.relative_bounding_box
        centres.append(bbox.ymin + bbox.height / 2)
    avg = sum(centres) / len(centres)
    return avg > 0.5  # faces in bottom half → put bubbles at top
