"""Text rendering — panel PNG + enriched JSON → panel PNG with overlays.

Three overlay kinds per panel:

* **Caption boxes** (CBML ``[caption ...]``): filled rectangles at one
  of six corner anchors, with bg/text colours from the CBML.
* **SFX** (CBML ``[sfx ...]``): styled free-floating text at one of
  seven anchors (six corners + ``center``). No background box; rendered
  with a contrasting outline for legibility on any background.
* **Dialogue bubbles** (CBML ``NAME: "text"``): per-type shape with text
  inside, placed within the speaker's ``bubble_layout`` region, and
  pointing a tail at any detected face inside that region.

Four bubble types, each with a distinct shape:

  ============ ====================================================
  speech       rounded rectangle + triangular tail toward speaker
  thought      cloud outline (overlapping ellipses) + trailing dots
  shout        starburst polygon (12-point star); no tail
  whisper      rectangle with dashed border; no tail
  ============ ====================================================

Face-aware placement: ``_detect_all_faces`` returns all face bboxes via
MediaPipe; each speaker's bubbles are placed in their ``bubble_layout``
region with top-vs-bottom chosen to avoid the face there. The tail
anchors at the face centre when a face is found inside the speaker's
region, or at the closest unique face when only one face exists in the
whole panel. Off-target speakers (no matchable face) skip the tail.

Best-effort caveat — for multi-character panels with no Phase 2
inpaint info, character identity is inferred by document-order matching
to the ``bubble_layout`` regions, not from per-character bounding boxes
in the generated image. Phase 2's ``inpaint_manager`` is expected to
write actual painted regions back to the panel JSON; this renderer will
prefer that truth source when present (see TODO at the top of
``_render_dialogue``).

The output PNG may be reviewed and hand-overwritten; the renderer skips
panels whose output already exists unless ``force=True``.
"""

from __future__ import annotations

import json
import math
import platform
from pathlib import Path
from typing import Any

import mediapipe as mp
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from lazycomics.models import Project

__all__ = ["render_text"]


def _font_paths_for_platform() -> list[str]:
    """System font search list, ordered so the host-platform paths come first.

    Reduces font-load latency on every panel: without this, Windows hosts
    burn four ``OSError``s working through Linux/macOS paths before landing
    on ``arialbd.ttf`` on every call.
    """
    win = [
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
    ]
    mac = [
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    linux = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    system = platform.system()
    if system == "Windows":
        return win + mac + linux
    if system == "Darwin":
        return mac + linux + win
    return linux + mac + win


# Common system font locations, host-platform paths first. First hit wins.
_FONT_PATHS = _font_paths_for_platform()


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
    img = _render_dialogue(
        img,
        panel.get("dialogue_lines") or [],
        panel.get("bubble_layout") or [],
    )
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


# ---------------------------------------------------------------------------
# Dialogue: per-speaker region placement with face-aware tails
# ---------------------------------------------------------------------------


def _render_dialogue(
    img: Image.Image,
    dialogue: list[dict[str, Any]],
    bubble_layout: list[dict[str, Any]],
) -> Image.Image:
    """Place bubbles in their speaker's ``bubble_layout`` region.

    TODO (Phase 2): when ``inpaint_manager`` writes actual painted
    character regions to the panel JSON (proposed field
    ``actual_character_regions``), prefer them over the predicted
    ``bubble_layout`` regions. The predicted regions are what we *asked*
    Phase 2 to paint; the actual regions are what got painted. Until that
    truth source exists, ``bubble_layout`` is the best signal available.
    """
    if not dialogue:
        return img

    draw = ImageDraw.Draw(img)
    W, H = img.size
    font = _load_font(size=max(14, W // 40))
    margin = 20
    padding = 12

    faces = _detect_all_faces(img)

    # Group dialogue by speaker, preserving the order of first appearance.
    speaker_lines: dict[str, list[dict[str, Any]]] = {}
    speaker_order: list[str] = []
    for line in dialogue:
        speaker = (line.get("character") or "").strip()
        if speaker not in speaker_lines:
            speaker_lines[speaker] = []
            speaker_order.append(speaker)
        speaker_lines[speaker].append(line)

    # Build a {speaker: (x, y, w, h)} pixel-rect map from bubble_layout,
    # auto-splitting the panel for any speakers without a declared region
    # (older enriched JSON or a mismatch between dialogue and bubble_layout).
    region_by_speaker = _pixel_regions(bubble_layout, W, H)
    _fill_missing_regions(region_by_speaker, speaker_order, W, H)

    for speaker in speaker_order:
        region = region_by_speaker.get(speaker, (0, 0, W, H))
        rx, ry, rw, rh = region

        # Per-region bubble width cap (don't exceed the region).
        max_bubble_width = min(int(W * 0.45), max(40, rw - 2 * margin))

        target_face = _select_speaker_face(faces, region, len(region_by_speaker))

        # Top-or-bottom within region, away from the face if we found one.
        if target_face is not None:
            face_cy = (target_face[1] + target_face[3]) // 2
            place_top = face_cy > (ry + rh // 2)
        else:
            place_top = True

        bubble_x = rx + margin
        y_cursor = ry + margin if place_top else ry + rh - margin

        for line in speaker_lines[speaker]:
            text = (line.get("text") or "").strip()
            if not text:
                continue
            bubble_type = line.get("bubble_type") or "speech"

            wrapped = _wrap_text(text, font, max_bubble_width - 2 * padding, draw)
            line_sizes = [_measure(draw, l, font) for l in wrapped]
            text_w = max(w for w, _ in line_sizes)
            text_h = sum(h for _, h in line_sizes)

            bubble_w = text_w + 2 * padding
            bubble_h = text_h + 2 * padding

            if place_top:
                bubble_y = y_cursor
                y_cursor = bubble_y + bubble_h + 10
            else:
                bubble_y = y_cursor - bubble_h
                y_cursor = bubble_y - 10

            _draw_bubble(draw, bubble_x, bubble_y, bubble_w, bubble_h, bubble_type)

            if target_face is not None:
                tx = (target_face[0] + target_face[2]) // 2
                ty = (target_face[1] + target_face[3]) // 2
                _draw_tail(
                    draw,
                    (bubble_x, bubble_y, bubble_x + bubble_w, bubble_y + bubble_h),
                    (tx, ty), bubble_type,
                )

            # Render the text last so it sits on top of bubble + tail fills.
            y_text = bubble_y + padding
            for l, (_, lh) in zip(wrapped, line_sizes):
                draw.text((bubble_x + padding, y_text), l, fill="#000000", font=font)
                y_text += lh

    return img


def _pixel_regions(
    bubble_layout: list[dict[str, Any]],
    W: int, H: int,
) -> dict[str, tuple[int, int, int, int]]:
    """Convert fractional ``BubbleRegion`` entries to pixel rects keyed by speaker."""
    out: dict[str, tuple[int, int, int, int]] = {}
    for r in bubble_layout:
        char = (r.get("character") or "").strip()
        if not char:
            continue
        rx = int(float(r.get("x_frac", 0.0)) * W)
        ry = int(float(r.get("y_frac", 0.0)) * H)
        rw = int(float(r.get("width_frac", 1.0)) * W)
        rh = int(float(r.get("height_frac", 1.0)) * H)
        out[char] = (rx, ry, rw, rh)
    return out


def _fill_missing_regions(
    region_by_speaker: dict[str, tuple[int, int, int, int]],
    speakers: list[str],
    W: int, H: int,
) -> None:
    """Fallback: auto-split the panel for any speakers without a region.

    Triggered when an older enriched JSON (pre-bubble_layout) is rendered,
    or when a dialogue line names a character that wasn't on ``panel.chars``.
    The split is the same default the enricher would have produced (equal
    horizontal slices in speaker-appearance order).
    """
    missing = [s for s in speakers if s not in region_by_speaker]
    if not missing:
        return
    # If only some speakers have regions, the split is no longer coherent.
    # Drop everything and re-split for all speakers in appearance order so
    # bubbles never overlap.
    if region_by_speaker:
        region_by_speaker.clear()
        missing = list(speakers)
    n = len(missing)
    if n == 0:
        return
    slice_w = W // n
    for i, s in enumerate(missing):
        # Last slice absorbs any rounding leftover so the total covers W.
        rw = W - i * slice_w if i == n - 1 else slice_w
        region_by_speaker[s] = (i * slice_w, 0, rw, H)


def _select_speaker_face(
    faces: list[tuple[int, int, int, int]],
    region: tuple[int, int, int, int],
    num_speakers: int,
) -> tuple[int, int, int, int] | None:
    """Pick the face most likely to be the speaker for this region.

    1. If any face's centre is inside the region, use that face.
    2. If only one face exists in the whole panel AND there's only one
       speaker, that's the speaker (region might be the whole panel).
    3. Otherwise return None — the renderer skips the tail rather than
       guessing wrong.
    """
    if not faces:
        return None

    rx, ry, rw, rh = region
    for face in faces:
        fcx = (face[0] + face[2]) // 2
        fcy = (face[1] + face[3]) // 2
        if rx <= fcx <= rx + rw and ry <= fcy <= ry + rh:
            return face

    if len(faces) == 1 and num_speakers <= 1:
        return faces[0]
    return None


# ---------------------------------------------------------------------------
# Bubble shapes (dispatcher + four implementations)
# ---------------------------------------------------------------------------


_BUBBLE_BORDER_WIDTH = {"speech": 2, "shout": 4, "whisper": 2, "thought": 2}


def _draw_bubble(draw: ImageDraw.ImageDraw, x: int, y: int,
                 w: int, h: int, bubble_type: str) -> None:
    """Dispatch to the per-type shape implementation."""
    border = _BUBBLE_BORDER_WIDTH.get(bubble_type, 2)
    if bubble_type == "thought":
        _draw_thought(draw, x, y, w, h, border)
    elif bubble_type == "shout":
        _draw_shout(draw, x, y, w, h, border)
    elif bubble_type == "whisper":
        _draw_whisper(draw, x, y, w, h, border)
    else:
        _draw_speech(draw, x, y, w, h, border)


def _draw_speech(draw: ImageDraw.ImageDraw, x: int, y: int,
                 w: int, h: int, border: int) -> None:
    """Rounded rectangle — the default speech bubble."""
    radius = min(20, h // 2)
    draw.rounded_rectangle(
        [x, y, x + w, y + h], radius=radius,
        fill="#ffffff", outline="#000000", width=border,
    )


def _draw_thought(draw: ImageDraw.ImageDraw, x: int, y: int,
                  w: int, h: int, border: int) -> None:
    """Cloud outline made of overlapping puff arcs around an ellipse."""
    cx = x + w / 2
    cy = y + h / 2
    n_puffs = 10
    rx = w / 2 * 0.85
    ry = h / 2 * 0.85

    points: list[tuple[float, float]] = []
    for i in range(n_puffs):
        a = 2 * math.pi * i / n_puffs - math.pi / 2
        puff_cx = cx + rx * math.cos(a)
        puff_cy = cy + ry * math.sin(a)
        next_a = 2 * math.pi * (i + 1) / n_puffs - math.pi / 2
        chord = math.hypot(
            rx * math.cos(next_a) - rx * math.cos(a),
            ry * math.sin(next_a) - ry * math.sin(a),
        )
        puff_r = chord * 0.6
        # Five points along this puff's outer half-arc.
        for j in range(5):
            t = -math.pi / 2 + math.pi * j / 4
            sample_a = a + t
            points.append((
                puff_cx + puff_r * math.cos(sample_a),
                puff_cy + puff_r * math.sin(sample_a),
            ))

    draw.polygon(points, fill="#ffffff", outline="#000000")
    if border > 1:
        for i in range(len(points)):
            draw.line(
                [points[i], points[(i + 1) % len(points)]],
                fill="#000000", width=border,
            )


def _draw_shout(draw: ImageDraw.ImageDraw, x: int, y: int,
                w: int, h: int, border: int) -> None:
    """12-point starburst polygon."""
    cx = x + w / 2
    cy = y + h / 2
    n_spikes = 12
    outer_rx = w / 2
    outer_ry = h / 2
    inner_rx = outer_rx * 0.68
    inner_ry = outer_ry * 0.68

    n_points = n_spikes * 2
    points: list[tuple[float, float]] = []
    for i in range(n_points):
        a = 2 * math.pi * i / n_points - math.pi / 2
        if i % 2 == 0:
            points.append((cx + outer_rx * math.cos(a), cy + outer_ry * math.sin(a)))
        else:
            points.append((cx + inner_rx * math.cos(a), cy + inner_ry * math.sin(a)))

    draw.polygon(points, fill="#ffffff", outline="#000000")
    if border > 1:
        for i in range(len(points)):
            draw.line(
                [points[i], points[(i + 1) % len(points)]],
                fill="#000000", width=border,
            )


def _draw_whisper(draw: ImageDraw.ImageDraw, x: int, y: int,
                  w: int, h: int, border: int) -> None:
    """Rectangle with a dashed black border."""
    draw.rectangle([x, y, x + w, y + h], fill="#ffffff")

    dash = 10
    gap = 6

    # Top + bottom edges.
    cx = x
    while cx < x + w:
        end = min(cx + dash, x + w)
        draw.line([(cx, y), (end, y)], fill="#000000", width=border)
        draw.line([(cx, y + h), (end, y + h)], fill="#000000", width=border)
        cx = end + gap

    # Left + right edges.
    cy = y
    while cy < y + h:
        end = min(cy + dash, y + h)
        draw.line([(x, cy), (x, end)], fill="#000000", width=border)
        draw.line([(x + w, cy), (x + w, end)], fill="#000000", width=border)
        cy = end + gap


# ---------------------------------------------------------------------------
# Tails
# ---------------------------------------------------------------------------


def _draw_tail(draw: ImageDraw.ImageDraw,
               bubble_rect: tuple[int, int, int, int],
               target: tuple[int, int],
               bubble_type: str) -> None:
    """Draw a tail/leader from ``bubble_rect`` toward ``target``.

    speech  -> triangular tail
    thought -> trailing dots (small circles between bubble and target)
    shout, whisper -> no tail (the shape itself carries the emphasis)
    """
    if bubble_type in ("shout", "whisper"):
        return

    bx0, by0, bx1, by1 = bubble_rect
    bcx = (bx0 + bx1) // 2
    tx, ty = target

    if ty > by1:
        origin_y = by1
    elif ty < by0:
        origin_y = by0
    else:
        # Target horizontally alongside the bubble — no clean tail direction.
        return

    border = _BUBBLE_BORDER_WIDTH.get(bubble_type, 2)

    if bubble_type == "thought":
        dx = tx - bcx
        dy = ty - origin_y
        for frac, radius in [(0.32, 7), (0.58, 5), (0.80, 3)]:
            px = int(bcx + dx * frac)
            py = int(origin_y + dy * frac)
            draw.ellipse(
                [px - radius, py - radius, px + radius, py + radius],
                fill="#ffffff", outline="#000000", width=border,
            )
        return

    # speech: filled triangle that overwrites the bubble outline at its base.
    tail_base = 14
    p1 = (bcx - tail_base // 2, origin_y)
    p2 = (bcx + tail_base // 2, origin_y)
    p3 = (tx, ty)
    draw.polygon([p1, p2, p3], fill="#ffffff")
    draw.line([p1, p3], fill="#000000", width=border)
    draw.line([p2, p3], fill="#000000", width=border)


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
# Face detection (module-level so tests can monkeypatch)
# ---------------------------------------------------------------------------


def _detect_all_faces(img: Image.Image) -> list[tuple[int, int, int, int]]:
    """Return ``(x0, y0, x1, y1)`` for every face MediaPipe detects.

    Empty list on no detections or on a detector error (logged loudly so
    the user notices). Module-level for monkeypatching in tests.
    """
    try:
        detector = mp.solutions.face_detection.FaceDetection(min_detection_confidence=0.5)
        arr = np.array(img.convert("RGB"))
        results = detector.process(arr)
    except Exception as e:
        print(f"[text] face detection error: {e}")
        return []

    if not results.detections:
        return []

    w, h = img.size
    out: list[tuple[int, int, int, int]] = []
    for det in results.detections:
        bbox = det.location_data.relative_bounding_box
        x0 = max(0, int(bbox.xmin * w))
        y0 = max(0, int(bbox.ymin * h))
        x1 = min(w, int((bbox.xmin + bbox.width) * w))
        y1 = min(h, int((bbox.ymin + bbox.height) * h))
        if x1 > x0 and y1 > y0:
            out.append((x0, y0, x1, y1))
    return out
