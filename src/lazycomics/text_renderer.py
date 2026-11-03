"""Text rendering — panel PNG + enriched JSON → panel PNG with overlays.

Three overlay kinds per panel:

* **Caption boxes** (CBML ``[caption ...]``): filled rectangles at one
  of six corner anchors, with bg/text colours from the CBML.
* **SFX** (CBML ``[sfx ...]``): styled free-floating text at one of
  seven anchors (six corners + ``center``). No background box; rendered
  with a contrasting outline for legibility on any background.
* **Dialogue bubbles** (CBML ``NAME: "text"``): per-type shape with text
  inside, laid out in CBML reading order near the speaker, with a short
  tail toward the speaker's face.

Four bubble types, each with a distinct shape:

  ============ ====================================================
  speech       rounded rectangle + triangular tail toward speaker
  thought      cloud (a ring of puffs) + trailing dots toward speaker
  shout        starburst (12-point star) + a spike toward speaker
  whisper      rectangle with dashed border + dashed tail
  ============ ====================================================

Tails leave by whichever side faces the speaker (sideways too), are sized
from the font, stop short of the face, and are routed around other
bubbles, captions and SFX. A speaker's consecutive lines of one type are
linked balloons, as in print: the first has the tail, each later one a
short connector to the one before (no tail pointing through it).

Face-aware placement: ``_detect_all_faces`` returns all face bboxes (hybrid
YuNet + MediaPipe). Panel characters are matched to faces by the enricher's
predicted left-to-right order alone (see ``_match_characters_to_faces``). A
speaker with no confident match gets no tail rather than a wrong one. A
panel the generator drew mirrored still gets mirrored tails: face identity
(OpenCV SFace) was tried and dropped in W7, because on this art it was
confidently wrong as often as right (Docs/PLAN.md problem 4).

Bubbles are then laid out together (``_layout_bubbles``) so they read in
CBML order by construction, each a step lower than the last, without
overlapping each other or the captions/SFX. Captions on one edge share it;
SFX move within their ``pos:`` zone to keep off faces.

The output PNG may be reviewed and hand-overwritten; the renderer skips
panels whose output already exists unless ``force=True``.

Alongside each output PNG it writes ``panels_text/<panel_id>.layout.json``:
what it placed and why (detected faces, the character→face matches, each
bubble's rect / tail anchor in CBML line order, caption and SFX boxes).
``lazycomics.review`` reads it to check reading order and overlaps without
re-deriving the layout. Its coordinates are in generated-panel px; when the
panel was lettered on its upscaled copy, ``scale`` is how much larger the
output PNG is.

It also writes the faces and matches it used back into the panel's enriched
JSON as ``detected_faces`` / ``actual_character_regions`` (with
``detected_faces_for``, a hash of the panel image). On a later render of the
*same* panel image they're reused instead of re-detected, so re-renders are
deterministic and a hand-corrected match sticks; a regenerated panel image
has a different hash, so stale matches are ignored. ``enrich()`` rewrites
the enriched JSON and clears them.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import platform
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from lazycomics import face_detector, upscaler
from lazycomics.config import cfg_get, load_config
from lazycomics.geometry import shape_hits_rect
from lazycomics.models import Project

__all__ = ["render_text"]


# ---------------------------------------------------------------------------
# Tunable defaults (three-tier: arg → config "text.<key>" → these)
# ---------------------------------------------------------------------------
#
# Font sizes are expressed as a fraction of the panel's size S = sqrt(W*H),
# then scaled by ``font_scale`` and floored at ``min_font_px``. Area-based
# rather than width-based so tall narrow panels (a 224x1024 strip) don't get
# the smallest text in the book. The ratios were tuned when every panel was
# 4:3 under width-scaling, so they're the old width ratios x sqrt(4/3): a 4:3
# panel keeps exactly its old size. Bump ``font_scale`` to enlarge everything
# at once, or set a per-type ratio for finer control.
_DEFAULT_DIALOGUE_FONT_RATIO = 0.035   # ~31px on a 1024x768 panel
_DEFAULT_CAPTION_FONT_RATIO = 0.032    # captions a touch smaller than dialogue
_DEFAULT_SFX_FONT_RATIO = 0.058        # SFX stay punchy/large
# Widest a bubble / caption may wrap to, as a fraction of S (same sqrt(4/3)
# conversion from the old 0.42 / 0.40 of width), capped at the panel width
# minus margins so narrow panels use most of their width.
_BUBBLE_MAX_WIDTH_FRAC = 0.485
_CAPTION_MAX_WIDTH_FRAC = 0.462
_DEFAULT_MIN_FONT_PX = 16              # never drop below this, even on tiny panels
_DEFAULT_FONT_SCALE = 1.0              # global multiplier on every text size
# Tails are sized from the dialogue font, so they keep their proportion to
# the text they attribute at any panel or page scale (a fixed 14px tail
# vanished on small slots: Docs/PLAN.md problem 21). Multiples of the font
# size, all scaled by ``tail_scale``. Comic tails are short stubs toward the
# speaker, not leader lines across the panel: they stop short of the face.
_DEFAULT_TAIL_SCALE = 1.0
_TAIL_BASE_FONT = 0.8      # width where the tail leaves the bubble
_TAIL_MIN_FONT = 1.3       # shortest tail, for a speaker right by the bubble
_TAIL_MAX_FONT = 2.5       # longest
_TRAIL_MIN_FONT = 1.8      # a thought's dots trail further, spaced out
_TRAIL_MAX_FONT = 3.5
_TAIL_FACE_GAP = 4         # px a tail stops short of the face box


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
    config: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Overlay text/bubbles/SFX onto each panel image.

    Reads ``<project>/panels/<panel_id>.png`` plus the matching enriched
    JSON; writes ``<project>/panels_text/<panel_id>.png``. Returns
    ``{panel_id: output_path}``.

    If ``upscale_panels`` has made ``panels_upscaled/<panel_id>.png``, that
    is what gets lettered: the layout is still decided on the generated
    panel (faces, sizes, every record in panel px), then drawn at the
    upscaled size, so the output is that size.

    Font sizes and tail length are resolved arg → ``text.<key>`` config →
    hardcoded default (see the module-level ``_DEFAULT_*`` constants).
    Pass ``config`` to avoid a redundant ``load_config()`` when the caller
    already has one; otherwise it's loaded internally.

    Panels whose output already exists are skipped (the user may have
    hand-edited them) unless ``force=True``.
    """
    if config is None:
        config = load_config()
    text_cfg = _resolve_text_cfg(config)

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
        panel_hash = hashlib.sha1(panel_path.read_bytes()).hexdigest()
        known = panel_data if panel_data.get("detected_faces_for") == panel_hash else {}
        img = Image.open(panel_path).convert("RGBA")
        canvas = _upscaled_canvas(project, panel_id, panel_hash, img.size)
        layout: dict[str, Any] = {"size": list(img.size)}
        if canvas is not None:
            layout["scale"] = canvas.width / img.width
        img = _render_one(img, panel_data, text_cfg, record=layout, known=known, canvas=canvas)
        (img if canvas is None else canvas).convert("RGB").save(out_path)
        out_path.with_suffix(".layout.json").write_text(
            json.dumps(layout, indent=2), encoding="utf-8",
        )
        if "faces" in layout:  # faces were looked for: persist what was decided
            panel_data["detected_faces"] = layout["faces"]
            if "regions" in layout:  # and matched to speakers (dialogue only)
                panel_data["actual_character_regions"] = layout["regions"]
            panel_data["detected_faces_for"] = panel_hash
            enriched_path.write_text(
                json.dumps(panel_data, indent=2, ensure_ascii=False), encoding="utf-8",
            )
        results[panel_id] = out_path

    return results


def _upscaled_canvas(project: Project, panel_id: str, panel_hash: str,
                     size: tuple[int, int]) -> Image.Image | None:
    """The upscaler's copy of the panel, to letter at full size; ``None`` if
    there isn't one (the panel itself is lettered).

    Raises if the copy was made from a different panel image (re-run
    ``upscale_panels``) or isn't a uniform enlargement of it. A copy with no
    recorded source (e.g. hand-edited) is used as is.
    """
    path = project.panels_upscaled_dir / f"{panel_id}.png"
    if not path.is_file():
        return None
    canvas = Image.open(path)
    made_from = canvas.info.get(upscaler.SOURCE_KEY)
    if made_from is not None and made_from != panel_hash:
        raise RuntimeError(
            f"{path} was upscaled from a different {panel_id}.png: "
            "re-run upscale_panels before render_text"
        )
    k = canvas.width / size[0]
    if k < 1 or abs(canvas.height - size[1] * k) > k:
        raise ValueError(
            f"{path} is {canvas.width}x{canvas.height}, not an enlargement of "
            f"the {size[0]}x{size[1]} panel"
        )
    return canvas.convert("RGBA")


def _resolve_text_cfg(config: dict[str, Any]) -> dict[str, float]:
    """Resolve the ``text.*`` knobs into a flat dict, with defaults."""
    return {
        "dialogue_ratio": cfg_get(config, "text.dialogue_font_ratio", _DEFAULT_DIALOGUE_FONT_RATIO),
        "caption_ratio": cfg_get(config, "text.caption_font_ratio", _DEFAULT_CAPTION_FONT_RATIO),
        "sfx_ratio": cfg_get(config, "text.sfx_font_ratio", _DEFAULT_SFX_FONT_RATIO),
        "min_font": cfg_get(config, "text.min_font_px", _DEFAULT_MIN_FONT_PX),
        "font_scale": cfg_get(config, "text.font_scale", _DEFAULT_FONT_SCALE),
        "tail_scale": cfg_get(config, "text.tail_scale", _DEFAULT_TAIL_SCALE),
    }


def _font_px(ratio: float, panel_w: int, panel_h: int,
             text_cfg: dict[str, float]) -> int:
    """Font size relative to the panel's sqrt(W*H), scaled and floored per config."""
    return max(int(text_cfg["min_font"]),
               int(math.sqrt(panel_w * panel_h) * ratio * text_cfg["font_scale"]))


def _wrap_width(frac: float, panel_w: int, panel_h: int, margin: int) -> int:
    """Max wrap width: ``frac`` of sqrt(W*H), but never wider than the panel allows."""
    return min(panel_w - 2 * margin, int(math.sqrt(panel_w * panel_h) * frac))


# ---------------------------------------------------------------------------
# Per-panel composition (order: captions, SFX, dialogue on top)
# ---------------------------------------------------------------------------


def _render_one(img: Image.Image, panel: dict[str, Any],
                text_cfg: dict[str, float],
                record: dict[str, Any] | None = None,
                known: dict[str, Any] | None = None,
                canvas: Image.Image | None = None) -> Image.Image:
    # With a ``canvas`` (the upscaled panel), everything is still laid out
    # and drawn on ``img`` exactly as without one, and also drawn, scaled, on
    # the canvas (_TeeDraw).
    # Captions and SFX are placed first; their boxes are obstacles the
    # dialogue layout keeps clear of, so record them even if the caller
    # didn't ask for a record.
    rec = record if record is not None else {}
    known = known or {}
    dialogue = panel.get("dialogue_lines") or []
    sfx = panel.get("sfx_lines") or []
    # Faces are found once, before anything is drawn over them.
    faces: list[tuple[int, int, int, int]] = []
    if known.get("detected_faces") is not None:
        faces = [tuple(f) for f in known["detected_faces"]]
    elif dialogue or sfx:
        faces = _detect_all_faces(img)
    img = _render_captions(img, panel.get("caption_boxes") or [], text_cfg, rec, canvas=canvas)
    img = _render_sfx(img, sfx, text_cfg, rec, faces=faces, obstacles=rec.get("captions", []),
                      canvas=canvas)
    img = _render_dialogue(
        img,
        dialogue,
        panel.get("bubble_layout") or [],
        text_cfg,
        rec,
        faces=faces,
        obstacles=rec.get("captions", []) + rec.get("sfx", []),
        known=known,
        canvas=canvas,
    )
    if sfx and "faces" not in rec:  # SFX-only panel: still record what it avoided
        rec["faces"] = [list(f) for f in faces]
    return img


def _record(record: dict[str, Any] | None, key: str, item: Any) -> None:
    """Append ``item`` to ``record[key]`` when the caller wants a layout record."""
    if record is not None:
        record.setdefault(key, []).append(item)


def _render_captions(img: Image.Image, captions: list[dict[str, Any]],
                     text_cfg: dict[str, float],
                     record: dict[str, Any] | None = None,
                     *, canvas: Image.Image | None = None) -> Image.Image:
    if not captions:
        return img
    draw = _draw_for(img, canvas)
    W, H = img.size
    font = _load_font(size=_font_px(text_cfg["caption_ratio"], W, H, text_cfg))
    line_h = _line_height(font)
    margin = 20
    padding = 10
    gap = 8
    widest = _wrap_width(_CAPTION_MAX_WIDTH_FRAC, W, H, margin)

    # Captions on the same edge split its width between the columns they ask
    # for (left / center / right), so neighbours can't overlap (problem 17);
    # two at the same anchor stack, in CBML order, away from the edge.
    items = []
    for cap in captions:
        text = (cap.get("text") or "").strip()
        if text:
            edge, _, col = (cap.get("position") or "top-left").partition("-")
            items.append((cap, text, "bottom" if edge == "bottom" else "top",
                          col if col in _CAPTION_COLUMNS else "left"))
    columns = {e: sorted({c for _, _, ie, c in items if ie == e}, key=_CAPTION_COLUMNS.index)
               for e in ("top", "bottom")}

    boxes: list[tuple[dict, list[str], str, str, int, int]] = []
    for cap, text, edge, col in items:
        n = len(columns[edge])
        share = (W - 2 * margin - gap * (n - 1)) // n
        lines = _wrap_text(text, font, max(20, min(widest, share) - 2 * padding), draw)
        box_w = max(_measure(draw, line, font)[0] for line in lines) + 2 * padding
        boxes.append((cap, lines, edge, col, box_w, line_h * len(lines) + 2 * padding))

    # Sides first, so a centred caption can sit between them.
    placed: dict[tuple[str, str], list[list[int]]] = {}
    rects: dict[int, list[int]] = {}
    for i, (_, _, edge, col, box_w, box_h) in sorted(enumerate(boxes),
                                                     key=lambda ib: ib[1][3] == "center"):
        if col == "left":
            x = margin
        elif col == "right":
            x = W - margin - box_w
        else:  # centred, but clear of the left and right columns' boxes
            left = [r[2] for r in placed.get((edge, "left"), [])]
            right = [r[0] for r in placed.get((edge, "right"), [])]
            x = max((W - box_w) // 2, max(left, default=margin - gap) + gap)
            x = min(x, min(right, default=W - margin + gap) - gap - box_w)
        stack = placed.setdefault((edge, col), [])
        if edge == "top":
            y = stack[-1][3] + gap if stack else margin
        else:
            y = stack[-1][1] - gap - box_h if stack else H - margin - box_h
        rects[i] = [x, y, x + box_w, y + box_h]
        stack.append(rects[i])

    for i, (cap, lines, *_rest) in enumerate(boxes):  # drawn and recorded in CBML order
        x, y = rects[i][:2]
        draw.rectangle(rects[i], fill=cap.get("bg_color") or "#000000")
        _record(record, "captions", rects[i])
        y_cursor = y + padding
        for line in lines:
            draw.text((x + padding, y_cursor), line, fill=cap.get("text_color") or "#ffffff", font=font)
            y_cursor += line_h
    return img


_CAPTION_COLUMNS = ["left", "center", "right"]


# Where an SFX may move to keep off a face, by its ``pos:`` anchor: the
# range its centre may take, as fractions of the panel width / height.
_SFX_ZONE_X = {"left": (0.0, 0.5), "center": (0.25, 0.75), "right": (0.5, 1.0)}
_SFX_ZONE_Y = {"top": (0.0, 0.45), "center": (0.2, 0.8), "bottom": (0.55, 1.0)}


def _render_sfx(img: Image.Image, sfx_lines: list[dict[str, Any]],
                text_cfg: dict[str, float],
                record: dict[str, Any] | None = None,
                faces: list[tuple[int, int, int, int]] | None = None,
                obstacles: list[list[int]] | None = None,
                *, canvas: Image.Image | None = None) -> Image.Image:
    """Draw each SFX at its ``pos:`` anchor, or as near it as keeps it off
    every face and caption.

    The script's anchor is a zone, not a point: a ``center`` SFX in a
    close-up would otherwise land on the face (problem 24). Each SFX may move
    within its anchor's zone (``_SFX_ZONE_*``); the position covering the
    least face and caption wins, the nearest to the anchor on a tie.
    """
    if not sfx_lines:
        return img
    draw = _draw_for(img, canvas)
    W, H = img.size
    font = _load_font(size=_font_px(text_cfg["sfx_ratio"], W, H, text_cfg))
    margin = 20
    avoid = [tuple(o) for o in obstacles or []]

    for sfx in sfx_lines:
        text = (sfx.get("text") or "").strip()
        if not text:
            continue
        color = sfx.get("color") or "#000000"
        pos = sfx.get("position") or "center"

        # Ink sits below the draw origin by the font's top bearing; place and
        # record where the glyphs actually land (plus the 2px outline).
        ox0, oy0, ox1, oy1 = draw.textbbox((0, 0), text, font=font)
        ink_w, ink_h = ox1 - ox0 + 4, oy1 - oy0 + 4
        x, y = _anchor_to_xy(pos, W, H, ink_w, ink_h, margin, allow_center=True)
        x, y = _sfx_position(x, y, ink_w, ink_h, pos, W, H, margin, faces or [], avoid)
        box = [x, y, x + ink_w, y + ink_h]
        avoid.append(tuple(box))
        _record(record, "sfx", box)
        x, y = x + 2 - ox0, y + 2 - oy0  # back to the draw origin

        # Outline for legibility against any background.
        outline = "#ffffff" if _is_dark(color) else "#000000"
        draw.text((x, y), text, fill=color, font=font, stroke_width=2, stroke_fill=outline)
    return img


def _sfx_position(x: int, y: int, w: int, h: int, pos: str, W: int, H: int, margin: int,
                  faces: list[tuple[int, int, int, int]],
                  avoid: list[tuple[int, int, int, int]]) -> tuple[int, int]:
    """Top-left for a ``w``×``h`` SFX box anchored at ``(x, y)``, moved within
    its zone off faces (each padded by a tenth of its height) and ``avoid``."""
    if not faces and not avoid:
        return x, y
    vert, _, horiz = pos.partition("-") if pos != "center" else ("center", "", "center")
    (zx0, zx1), (zy0, zy1) = _SFX_ZONE_X.get(horiz, (0.0, 1.0)), _SFX_ZONE_Y.get(vert, (0.0, 1.0))
    lo_x, hi_x = max(margin, int(zx0 * W - w / 2)), min(W - margin - w, int(zx1 * W - w / 2))
    lo_y, hi_y = max(margin, int(zy0 * H - h / 2)), min(H - margin - h, int(zy1 * H - h / 2))
    pad = [(f[0] - (f[3] - f[1]) // 10, f[1] - (f[3] - f[1]) // 10,
            f[2] + (f[3] - f[1]) // 10, f[3] + (f[3] - f[1]) // 10) for f in faces]

    def cost(cx: int, cy: int) -> float:
        rect = (cx, cy, cx + w, cy + h)
        hit = sum(_overlap_area(rect, f) for f in pad) + sum(_overlap_area(rect, a) for a in avoid)
        return hit * 100 + math.hypot(cx - x, cy - y)

    steps = 12
    cands = {(x, y)} | {
        (lo_x + (hi_x - lo_x) * i // steps, lo_y + (hi_y - lo_y) * j // steps)
        for i in range(steps + 1) for j in range(steps + 1) if lo_x <= hi_x and lo_y <= hi_y
    }
    return min(cands, key=lambda c: cost(*c))


# ---------------------------------------------------------------------------
# Dialogue: who is where, then a reading-order-correct layout
# ---------------------------------------------------------------------------


# Faces shorter than this fraction of the tallest face are treated as
# background extras and never matched to a named character.
_MIN_FACE_FRAC = 0.5
# Distance matching: a face whose distance to another character's predicted
# centre is within this fraction of the panel width of its own match's is
# ambiguous, and that speaker gets no tail (fail closed).
_AMBIGUOUS_FRAC = 0.05
_BUBBLE_GAP = 14
# Wrap widths a bubble may take, as fractions of the widest: the first that
# lets the layout keep it off faces, captions and SFX.
_NARROWER = (1.0, 0.7, 0.5)


def _render_dialogue(
    img: Image.Image,
    dialogue: list[dict[str, Any]],
    bubble_layout: list[dict[str, Any]],
    text_cfg: dict[str, float],
    record: dict[str, Any] | None = None,
    *,
    faces: list[tuple[int, int, int, int]] | None = None,
    obstacles: list[list[int]] | None = None,
    known: dict[str, Any] | None = None,
    canvas: Image.Image | None = None,
) -> Image.Image:
    """Lay out and draw the dialogue bubbles, in CBML reading order.

    1. **Who is where.** Each panel character is matched to a detected face
       (:func:`_match_characters_to_faces`) so tails point at the right
       speaker, by predicted position; a speaker with no confident match
       gets no tail and a bubble
       near their predicted ``bubble_layout`` region instead. ``known``
       (``detected_faces`` / ``actual_character_regions`` persisted from an
       earlier render of the same panel image, or hand edits) overrides
       detection and matching.
    2. **Where bubbles go.** :func:`_layout_bubbles` places all bubbles at
       once so they read in CBML order by construction, never overlap each
       other or the captions/SFX in ``obstacles``, and otherwise sit near
       their speaker over flat negative space, clear of faces.

    Bubble shapes are sized so their text is fully contained (clouds and
    starbursts are drawn larger than their text box), and tails are short
    stubs toward the speaker, routed around every other box. Writes
    ``font_px``, ``faces``, ``regions`` and one ``bubbles`` entry per drawn
    line into ``record`` (with the tail's geometry, or why there's none).
    """
    if not dialogue:
        return img

    draw = _draw_for(img, canvas)
    W, H = img.size
    font_px = _font_px(text_cfg["dialogue_ratio"], W, H, text_cfg)
    font = _load_font(size=font_px)
    line_h = _line_height(font)
    margin = 20
    padding = 12
    sizes = _tail_sizes(font_px, text_cfg["tail_scale"])
    max_bubble_width = _wrap_width(_BUBBLE_MAX_WIDTH_FRAC, W, H, margin)
    known = known or {}
    if faces is None:
        faces = ([tuple(f) for f in known["detected_faces"]]
                 if known.get("detected_faces") is not None else _detect_all_faces(img))

    # Every panel character in CBML order (bubble_layout), plus any speaker
    # it doesn't name. Silent characters count: they occupy faces too.
    characters = [(r.get("character") or "").strip() for r in bubble_layout]
    characters = [c for c in characters if c]
    for line in dialogue:
        speaker = (line.get("character") or "").strip()
        if speaker and speaker not in characters:
            characters.append(speaker)

    # The enricher's bubble_layout is a *prediction* (equal horizontal slices
    # in character order): the left-to-right prior for matching, and the
    # fallback anchor for a speaker we can't place.
    region_by_char = _pixel_regions(bubble_layout, W, H)
    _fill_missing_regions(region_by_char, characters, W, H)

    if known.get("actual_character_regions") is not None:
        regions = {c: tuple(r) for c, r in known["actual_character_regions"].items()}
    else:
        regions = _match_characters_to_faces(characters, region_by_char, faces, W)
    # A matched region is a face even if the detector missed it (a hand
    # correction for a missed face): keep bubbles off it like any other.
    faces = list(faces) + [r for r in regions.values() if r not in faces]

    if record is not None:
        record["font_px"] = font_px
        record["faces"] = [list(f) for f in faces]
        record["regions"] = {c: list(r) for c, r in regions.items()}

    def fit(text: str, bubble_type: str, narrow: int) -> dict[str, Any]:
        """Wrapped lines and footprint for ``text``, at the ``narrow``-th of
        ``_NARROWER``'s wrap widths.

        Cloud (thought) and starburst (shout) need their text box smaller
        than the drawn shape or the outline clips the glyphs: we wrap to the
        shape's *interior* width, then inflate the footprint back out by the
        same fraction. Speech/whisper fill their rect (interior 1.0).
        """
        wf, hf = _BUBBLE_INTERIOR.get(bubble_type, (1.0, 1.0))
        wrap_width = max(20, int((max_bubble_width * _NARROWER[narrow] - 2 * padding) * wf))
        wrapped = _wrap_text(text, font, wrap_width, draw)
        text_w = max(_measure(draw, l, font)[0] for l in wrapped)
        text_h = line_h * len(wrapped)
        return {"lines": wrapped, "text_h": text_h,
                "w": min(int((text_w + 2 * padding) / wf), W - 2 * margin),
                "h": min(int((text_h + 2 * padding) / hf), H - 2 * margin)}

    # Size every bubble first: the layout needs them all at once.
    bubbles: list[dict[str, Any]] = []
    for line_no, line in enumerate(dialogue):
        speaker = (line.get("character") or "").strip()
        text = (line.get("text") or "").strip()
        if not text or not speaker:
            continue
        bubble_type = line.get("bubble_type") or "speech"
        if speaker in regions:
            fx0, fy0, fx1, fy1 = regions[speaker]
            anchor, has_face, face = ((fx0 + fx1) // 2, (fy0 + fy1) // 2), True, regions[speaker]
        else:
            rx, ry, rw, rh = region_by_char[speaker]
            anchor, has_face, face = (rx + rw // 2, ry + rh // 2), False, None

        bubbles.append({
            "line": line_no, "speaker": speaker, "type": bubble_type, "text": text,
            "anchor": anchor, "has_face": has_face, "face": face, "narrow": 0,
            **fit(text, bubble_type, 0),
        })

    # A speaker's run of same-type lines: linked balloons, tail on the first.
    for prev, b in zip(bubbles, bubbles[1:]):
        if (b["speaker"], b["type"]) == (prev["speaker"], prev["type"]):
            b["link"] = prev["line"]

    busy = _busyness_map(img)
    boxes = [tuple(o) for o in obstacles or []]
    positions, cost = _layout_bubbles(bubbles, faces, boxes, busy, W, H, margin,
                                      sizes=sizes, stagger=line_h)
    # A bubble still landing on a face or a caption/SFX is re-wrapped
    # narrower (and so taller), as a letterer reshapes a balloon to fit the
    # space; the new layout is kept only if it costs less overall.
    for _ in range(len(_NARROWER)):
        stuck = {i for i, (b, (x, y)) in enumerate(zip(bubbles, positions))
                 if b["narrow"] + 1 < len(_NARROWER)
                 and any(_overlap_area((x, y, x + b["w"], y + b["h"]), f) for f in list(faces) + boxes)}
        if not stuck:
            break
        trial = [{**b, "narrow": b["narrow"] + 1, **fit(b["text"], b["type"], b["narrow"] + 1)}
                 if i in stuck else b for i, b in enumerate(bubbles)]
        trial_positions, trial_cost = _layout_bubbles(trial, faces, boxes, busy, W, H, margin,
                                                      sizes=sizes, stagger=line_h)
        if trial_cost >= cost:
            break
        bubbles, positions, cost = trial, trial_positions, trial_cost
    rects = [(x, y, x + b["w"], y + b["h"]) for b, (x, y) in zip(bubbles, positions)]
    if record is not None and cost == math.inf:  # too much text: stacked, overlapping
        record["overflow"] = round(sum(b["h"] + _BUBBLE_GAP for b in bubbles) / (H - 2 * margin), 2)

    for i, (b, rect) in enumerate(zip(bubbles, rects)):
        x, y = rect[:2]
        w, h = b["w"], b["h"]
        _draw_bubble(draw, x, y, w, h, b["type"])

        # Only draw a tail when we actually located the speaker (a matched
        # face). With no face the anchor is a guess, so a tail would point
        # at an arbitrary spot — we omit it rather than mislead.
        tail = None
        if "link" in b:
            _draw_link(draw, rects[i - 1], rect, b["type"], sizes["base"] * 3 // 4)
        elif b["has_face"]:
            avoid = [r for j, r in enumerate(rects) if j != i] + boxes
            tail = _tail_shape(rect, b["face"], b["type"], sizes, avoid=avoid, bounds=(W, H))
            _draw_tail(draw, tail, b["type"])
        entry = {
            "line": b["line"], "speaker": b["speaker"], "type": b["type"],
            "rect": list(rect), "anchor": list(b["anchor"]), "tail": tail,
        }
        if "link" in b:
            entry["linked_to"] = b["link"]
        if tail is None:
            entry["no_tail"] = "linked" if "link" in b else "unmatched"
        _record(record, "bubbles", entry)

        # Render the text last so it sits on top of bubble + tail fills.
        # Speech/whisper: left-aligned at the padding inset. Thought/shout:
        # centred inside the inflated shape, clear of the cloud / spikes.
        centred = b["type"] in ("thought", "shout")
        y_text = (y + (h - b["text_h"]) // 2) if centred else (y + padding)
        for l in b["lines"]:
            lw = _measure(draw, l, font)[0]
            x_text = (x + (w - lw) // 2) if centred else (x + padding)
            draw.text((x_text, y_text), l, fill="#000000", font=font)
            y_text += line_h

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


def _match_characters_to_faces(
    characters: list[str],
    region_by_char: dict[str, tuple[int, int, int, int]],
    faces: list[tuple[int, int, int, int]],
    W: int,
) -> dict[str, tuple[int, int, int, int]]:
    """Map panel characters to detected face boxes.

    The prior is the enricher's predicted left-to-right order of *all* panel
    characters (silent ones too — they occupy faces). Faces shorter than
    ``_MIN_FACE_FRAC`` of the tallest are dropped as background extras.
    Then:

    * as many faces as characters: match by rank (leftmost predicted
      character ↔ leftmost face, and so on);
    * otherwise: the assignment minimising the total horizontal distance from
      each face to its character's predicted region centre, dropping any
      match that's ambiguous (another character's centre nearly as close) so
      that speaker gets no tail rather than a wrong one.

    Characters left unmatched are absent from the result.
    """
    if not faces or not characters:
        return {}
    tallest = max(f[3] - f[1] for f in faces)
    cands = [tuple(f) for f in faces if (f[3] - f[1]) >= _MIN_FACE_FRAC * tallest]
    cands = sorted(cands, key=lambda f: f[1] - f[3])[:8]  # tallest first; bounds the search

    def face_x(f):
        return (f[0] + f[2]) / 2

    centre = {c: region_by_char[c][0] + region_by_char[c][2] / 2 for c in characters}

    if len(cands) == len(characters):
        by_x = sorted(characters, key=lambda c: centre[c])
        return dict(zip(by_x, sorted(cands, key=face_x)))

    if len(characters) <= len(cands):
        options = ([*zip(characters, p)] for p in itertools.permutations(cands, len(characters)))
    else:
        options = ([*zip(p, cands)] for p in itertools.permutations(characters, len(cands)))
    best = min(options, key=lambda pairs: sum(abs(face_x(f) - centre[c]) for c, f in pairs))

    matched: dict[str, tuple[int, int, int, int]] = {}
    for c, f in best:
        d = abs(face_x(f) - centre[c])
        if any(abs(face_x(f) - centre[o]) - d < _AMBIGUOUS_FRAC * W
               for o in characters if o != c):
            continue
        matched[c] = f
    return matched


def _busyness_map(img: Image.Image, grid_w: int = 40):
    """A coarse map of visual detail, for steering bubbles to empty space.

    Returns ``(sat, grid_w, grid_h)``: a summed-area table over a
    ``grid_w``-wide grid where each cell holds the average edge energy of the
    corresponding panel region (0 = flat background, high = busy
    foreground/faces). Computed with an edge filter then box-downsampled, so
    it costs one filter pass however many placements are scored, and the
    table makes each rect's mean O(1). On a flat test image it is uniformly
    zero, so placement stays deterministic.
    """
    grid_h = max(1, int(grid_w * img.height / max(1, img.width)))
    try:
        edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
        small = edges.resize((grid_w, grid_h), resample=Image.BOX)
    except Exception:
        small = Image.new("L", (grid_w, grid_h), 0)
    sat = [[0] * (grid_w + 1) for _ in range(grid_h + 1)]
    for cy in range(grid_h):
        row = 0
        for cx in range(grid_w):
            row += small.getpixel((cx, cy))
            sat[cy + 1][cx + 1] = sat[cy][cx + 1] + row
    return sat, grid_w, grid_h


def _busy_in_rect(busy, W: int, H: int,
                  rect: tuple[int, int, int, int]) -> float:
    """Mean edge energy (0..1) of the busyness map under ``rect``."""
    sat, gw, gh = busy
    x0, y0, x1, y1 = rect
    cx0 = max(0, min(gw - 1, int(x0 / max(1, W) * gw)))
    cx1 = max(0, min(gw - 1, int(x1 / max(1, W) * gw)))
    cy0 = max(0, min(gh - 1, int(y0 / max(1, H) * gh)))
    cy1 = max(0, min(gh - 1, int(y1 / max(1, H) * gh)))
    total = (sat[cy1 + 1][cx1 + 1] - sat[cy0][cx1 + 1]
             - sat[cy1 + 1][cx0] + sat[cy0][cx0])
    n = (cx1 - cx0 + 1) * (cy1 - cy0 + 1)
    return (total / n) / 255.0 if n else 0.0


def _overlap_area(a: tuple[int, int, int, int],
                  b: tuple[int, int, int, int]) -> int:
    """Area of the intersection of two (x0, y0, x1, y1) rects (0 if none)."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy


def _rect_gap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Distance between two (x0, y0, x1, y1) rects (0 if they touch or overlap)."""
    dx = max(b[0] - a[2], 0, a[0] - b[2])
    dy = max(b[1] - a[3], 0, a[1] - b[3])
    return math.hypot(dx, dy)


# Layout cost weights, per px² of overlap unless noted. A bubble on a face or
# on a caption/SFX costs ten times what it does over busy art; a tail that
# runs into another box costs about as much as hiding a small face.
_FACE_COST = 10.0
_OBSTACLE_COST = 10.0
_CLEARANCE_COST = 1.0      # within a tail's length of a face: no room for the tail
_BUSY_COST = 1.2
_REACH_COST = 100.0        # per px a bubble sits beyond its tail's reach of the face
_CROSS_COST = 20000.0      # per tail that crosses a bubble, caption or SFX


def _layout_bubbles(
    bubbles: list[dict[str, Any]],
    faces: list[tuple[int, int, int, int]],
    obstacles: list[list[int]],
    busy,
    W: int, H: int, margin: int,
    *,
    sizes: dict[str, int],
    stagger: int = 0,
) -> tuple[list[tuple[int, int]], float]:
    """Place every bubble at once so the panel reads in CBML order.

    (a) **Bands.** Lines go into vertical bands, top to bottom in CBML
        order. A line may share the previous line's band, to its right, when
        a different speaker standing further right says it — the Z-path of
        an alternating exchange. A speaker's consecutive lines always stack.
        Within a band each bubble starts ``stagger`` (a text line) lower than
        the one before, so no two consecutive lines start level and a
        speaker's lines either side of another's never read as one speech
        (problem 23). Every allowed sharing pattern is tried; the cheapest
        wins.
    (b) **Positions.** Each band sits strictly below the previous one. Band
        offsets are chosen jointly (a DP over candidate heights) and each
        bubble's horizontal position within its band greedily, all by one
        cost: covering a face (``_FACE_COST``, the strongest term; problem
        18), sitting within a tail's length of one, covering captions/SFX,
        sitting over busy detail, distance from the bubble's bottom-centre
        to its speaker (above them, by preference), sitting beyond tail
        reach of the speaker's face, and a tail (as :func:`_tail_shape`
        would draw it) running into a caption/SFX or a bubble in the same
        or the neighbouring band. A linked balloon (``"link"``: the same
        speaker's previous line) has no tail, so only its distance to the
        speaker counts.

    Reading order and bubble/bubble non-overlap hold by construction.
    Returns top-left ``(x, y)`` per bubble, in input order, and the layout's
    cost (``inf`` when the text can't be stacked in the panel at all).
    """
    if not bubbles:
        return [], 0.0
    gap = _BUBBLE_GAP
    usable_w = W - 2 * margin
    step = max(8, (H - 2 * margin) // 40)
    clearance = sizes["lo"]
    keep_out = [(f[0] - clearance, f[1] - clearance, f[2] + clearance, f[3] + clearance)
                for f in faces]
    obst = [tuple(o) for o in obstacles]
    reach_of = {"thought": sizes["trail_hi"]}
    longest = max(sizes["hi"], sizes["trail_hi"])

    tails: dict[tuple[int, int, int], dict[str, Any] | None] = {}

    def tail(i: int, x: int, y: int) -> dict[str, Any] | None:
        """Bubble ``i``'s unrouted tail at ``(x, y)`` (cached)."""
        if (i, x, y) not in tails:
            b = bubbles[i]
            tails[i, x, y] = (_tail_shape((x, y, x + b["w"], y + b["h"]), b["face"], b["type"],
                                          sizes, bounds=(W, H))
                              if b["face"] and "link" not in b else None)
        return tails[i, x, y]

    def crosses(i: int, x: int, y: int, rects) -> bool:
        shape = tail(i, x, y)
        return shape is not None and any(_tail_hits(shape, r, bubbles[i]["anchor"]) for r in rects)

    def rect_cost(i: int, x: int, y: int) -> float:
        b = bubbles[i]
        w, h = b["w"], b["h"]
        rect = (x, y, x + w, y + h)
        ax, ay = b["anchor"]
        cost = sum(_overlap_area(rect, f) for f in faces) * _FACE_COST
        cost += sum(_overlap_area(rect, f) for f in keep_out) * _CLEARANCE_COST
        cost += sum(_overlap_area(rect, o) for o in obst) * _OBSTACLE_COST
        cost += _busy_in_rect(busy, W, H, rect) * w * h * _BUSY_COST
        cost += math.hypot(x + w / 2 - ax, y + h - ay)
        if b["face"] and "link" not in b:
            reach = reach_of.get(b["type"], sizes["hi"]) + gap
            cost += max(0.0, _rect_gap(rect, b["face"]) - reach) * _REACH_COST
            if obst and crosses(i, x, y, obst):
                cost += _CROSS_COST
        return cost

    def placed(band: tuple[int, ...], y: int, xs: list[int]) -> list[tuple[int, int, int]]:
        """``(bubble, x, y)`` for each bubble of ``band`` placed at ``y``."""
        return [(i, x, y + k * stagger) for k, (i, x) in enumerate(zip(band, xs))]

    def rect_of(i: int, x: int, y: int) -> tuple[int, int, int, int]:
        return (x, y, x + bubbles[i]["w"], y + bubbles[i]["h"])

    def cross_cost(upper, lower) -> float:
        """Tails of either group that run into the other's bubbles, plus how
        far a linked balloon in ``lower`` sits sideways off the one it joins."""
        line_x = {bubbles[i]["line"]: x + bubbles[i]["w"] / 2 for i, x, _ in upper}
        offset = sum(abs(x + bubbles[i]["w"] / 2 - line_x[bubbles[i]["link"]])
                     for i, x, _ in lower if bubbles[i].get("link") in line_x)
        top = min(y for _, _, y in lower)
        if top - max(y + bubbles[i]["h"] for i, _, y in upper) > longest:
            return offset
        up_r = [rect_of(*p) for p in upper]
        low_r = [rect_of(*p) for p in lower]
        n = sum(crosses(i, x, y, low_r) for i, x, y in upper)
        n += sum(crosses(i, x, y, up_r) for i, x, y in lower)
        return n * _CROSS_COST + offset

    band_cache: dict[tuple[tuple[int, ...], int], tuple[float, list[int]]] = {}

    def band_cost(band: tuple[int, ...], y: int) -> tuple[float, list[int]]:
        """Cheapest left-to-right placement of ``band``'s bubbles at height ``y``."""
        key = (band, y)
        if key not in band_cache:
            total, xs, lo, done = 0.0, [], margin, []
            for k, i in enumerate(band):
                b = bubbles[i]
                by = y + k * stagger
                rest = sum(bubbles[j]["w"] + gap for j in band[k + 1:])
                hi = max(lo, W - margin - b["w"] - rest)
                # Near the speaker: centred on them, or offset by up to half
                # a bubble + 24px either side, or just beside their face
                # (a sideways tail). Clamped to the band's free span.
                centred = b["anchor"][0] - b["w"] // 2
                reach = b["w"] // 2 + 24
                cands = {centred + reach * n // 4 for n in range(-4, 5)}
                if b["face"]:
                    cands |= {b["face"][0] - clearance - gap - b["w"], b["face"][2] + clearance + gap}
                cands = {min(hi, max(lo, c)) for c in cands}

                def cost(cx: int, i: int = i, by: int = by) -> float:
                    c = rect_cost(i, cx, by)
                    if done:
                        c += cross_cost(done, [(i, cx, by)])
                    return c

                x = min(sorted(cands), key=cost)
                total += cost(x)
                xs.append(x)
                done.append((i, x, by))
                lo = x + b["w"] + gap
            band_cache[key] = (total, xs)
        return band_cache[key]

    def heights(band: tuple[int, ...]) -> int:
        return max(k * stagger + bubbles[i]["h"] for k, i in enumerate(band))

    def y_candidates(band: tuple[int, ...], lo: int, hi: int) -> list[int]:
        """Heights near the band's speakers: up to six bubble-slots above the
        head, three below (the old placer's reach), plus just clear of the
        head above and below. Only if reading order rules all of those out
        does the band search the whole ``[lo, hi]``.
        """
        hb = heights(band)
        grid = set(range(lo, hi + 1, step)) | {hi}
        near, snug = set(), set()
        for i in band:
            if bubbles[i]["face"]:
                _, top, _, bottom = bubbles[i]["face"]
                above, below = top - clearance - gap - hb, bottom + clearance + gap
            else:
                above = below = bubbles[i]["anchor"][1] - hb
            snug.update((above, below))  # just clear of the head, for a tight fit
            near.update(y for y in grid
                        if above - 5 * (hb + gap) <= y <= below + 2 * (hb + gap))
        ys = {y for y in near | snug if lo <= y <= hi}
        return sorted(ys or grid)

    def solve(bands: list[tuple[int, ...]]) -> tuple[float, list[tuple[int, list[int]]]] | None:
        """DP over band heights; ``None`` if the bands can't fit the panel."""
        hs = [heights(b) for b in bands]
        if sum(hs) + gap * (len(bands) - 1) > H - 2 * margin:
            return None
        # Latest top each band may take and still leave room for those below.
        latest = [H - margin - sum(hs[k:]) - gap * (len(bands) - 1 - k)
                  for k in range(len(bands))]
        prev: list[tuple[int, float, list]] = []   # (y, cost, placements so far)
        for k, band in enumerate(bands):
            earliest = margin if k == 0 else min(y for y, _, _ in prev) + hs[k - 1] + gap
            cur = []
            for y in y_candidates(band, earliest, latest[k]):
                cost, xs = band_cost(band, y)
                if k == 0:
                    cur.append((y, cost, [(y, xs)]))
                    continue
                here = placed(band, y, xs)
                ok = [(p[1] + cross_cost(placed(bands[k - 1], *p[2][-1]), here), p)
                      for p in prev if p[0] + hs[k - 1] + gap <= y]
                if ok:
                    total, best = min(ok, key=lambda o: o[0])
                    cur.append((y, total + cost, best[2] + [(y, xs)]))
            if not cur:
                return None
            prev = cur
        best = min(prev, key=lambda p: p[1])
        return best[1], best[2]

    def structures(i: int, acc: list[list[int]]):
        """Every band structure: each line opens a band or (Z-path) joins the last."""
        if i == len(bubbles):
            yield [tuple(b) for b in acc]
            return
        b = bubbles[i]
        if acc:
            last = bubbles[acc[-1][-1]]
            width = sum(bubbles[j]["w"] + gap for j in acc[-1]) + b["w"]
            if (b["speaker"] != last["speaker"]
                    and b["anchor"][0] > last["anchor"][0] and width <= usable_w):
                yield from structures(i + 1, acc[:-1] + [acc[-1] + [i]])
        yield from structures(i + 1, acc + [[i]])

    best: tuple[float, list[tuple[int, ...]], list] | None = None
    for bands in structures(0, []):
        solved = solve(bands)
        if solved and (best is None or solved[0] < best[0]):
            best = (solved[0], bands, solved[1])

    positions: dict[int, tuple[int, int]] = {}
    if best is not None:
        cost, bands, placements = best
        for band, (y, xs) in zip(bands, placements):
            for i, x, by in placed(band, y, xs):
                positions[i] = (x, by)
    else:
        cost = math.inf
        # Too much text to stack in the panel: keep the order, stack from the
        # top as far as it goes, and let the bottom bands overlap.
        y = margin
        for i, b in enumerate(bubbles):
            _, xs = band_cost((i,), min(y, H - margin - b["h"]))
            positions[i] = (xs[0], min(y, H - margin - b["h"]))
            y += b["h"] + gap
    return [positions[i] for i in range(len(bubbles))], cost


# ---------------------------------------------------------------------------
# Bubble shapes (dispatcher + four implementations)
# ---------------------------------------------------------------------------


_BUBBLE_BORDER_WIDTH = {"speech": 2, "shout": 4, "whisper": 2, "thought": 2}

# Fraction of the bubble footprint that's safe for text, per shape. Speech
# and whisper fill their rect (1.0). Thought clouds and shout starbursts
# draw outline geometry *outside* an inner ellipse, so their text must fit
# in a smaller box — we wrap to this fraction and inflate the footprint by
# its inverse so the drawn shape fully surrounds the glyphs.
_BUBBLE_INTERIOR = {
    "speech": (1.0, 1.0),
    "whisper": (1.0, 1.0),
    "thought": (0.55, 0.55),
    "shout": (0.60, 0.54),
}


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
    """Cloud: a ring of equal puffs around a central ellipse, filling its box.

    Drawn exactly inside ``(x, y, w, h)``, so the box the layout and the
    checks reason about is the cloud as drawn. (The previous cloud's puffs
    overshot their box by up to a sixth, onto captions and faces.) Puff
    radius is 0.4 of the box's shorter half; the notches between puffs then
    stay outside an inner ellipse that holds a ``_BUBBLE_INTERIOR`` text box
    at any aspect. Each puff is drawn as a black disc, then a white one
    ``border`` smaller, then the centre ellipse in white, which leaves only
    the outer arcs outlined.
    """
    a, b = w / 2, h / 2
    cx, cy = x + a, y + b
    r = max(3.0, 0.4 * min(a, b) - border)
    ea, eb = max(1.0, a - r - border), max(1.0, b - r - border)
    # Puff centres at equal arc length round the centre ellipse, close enough
    # (1.6 r apart) that neighbouring puffs overlap.
    ring = [(cx + ea * math.cos(2 * math.pi * k / 360), cy + eb * math.sin(2 * math.pi * k / 360))
            for k in range(361)]
    arc = [0.0]
    for p0, p1 in zip(ring, ring[1:]):
        arc.append(arc[-1] + math.dist(p0, p1))
    n = max(7, math.ceil(arc[-1] / (1.6 * r)))
    centres, k = [], 0
    for m in range(n):
        target = arc[-1] * (m + 0.5) / n
        while arc[k + 1] < target:
            k += 1
        centres.append(ring[k])
    for px, py in centres:
        draw.ellipse([px - r - border, py - r - border, px + r + border, py + r + border], fill="#000000")
    for px, py in centres:
        draw.ellipse([px - r, py - r, px + r, py + r], fill="#ffffff")
    draw.ellipse([cx - ea, cy - eb, cx + ea, cy + eb], fill="#ffffff")


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


def _tail_sizes(font_px: int, scale: float = 1.0) -> dict[str, int]:
    """Tail dimensions for a panel's dialogue font (see ``_TAIL_*``), px."""
    f = font_px * scale
    return {"base": max(6, round(_TAIL_BASE_FONT * f)),
            "lo": round(_TAIL_MIN_FONT * f), "hi": round(_TAIL_MAX_FONT * f),
            "trail_lo": round(_TRAIL_MIN_FONT * f), "trail_hi": round(_TRAIL_MAX_FONT * f),
            "dot": max(3, round(0.36 * f))}


def _tail_shape(rect: tuple[int, int, int, int], face: tuple[int, int, int, int],
                bubble_type: str, sizes: dict[str, int], *,
                avoid=(), bounds: tuple[int, int] | None = None) -> dict[str, Any]:
    """Geometry of the tail from bubble ``rect`` toward ``face``.

    The tail leaves by an edge the face lies beyond (furthest first:
    beneath, above, or beside the bubble), rooted as close to the face as
    the edge allows, and points at the face's centre. Its length is the gap
    to the face, less ``_TAIL_FACE_GAP``, held to ``sizes``' range. The
    first candidate that neither runs into nor points through (on its way
    to the face's centre) any rect in ``avoid`` wins; if none does, the
    preferred one is kept.

    Returns ``{"points": [base, base, tip]}`` (speech, whisper, and a
    shout's spike, which is rooted inside the starburst), or ``{"root":
    [x, y], "dots": [[x, y, r], ...]}`` for a thought's trailing dots.
    """
    aim = ((face[0] + face[2]) / 2, (face[1] + face[3]) / 2)
    first = None
    for shape in _tail_candidates(rect, face, bubble_type, sizes, bounds):
        if first is None:
            first = shape
        if not any(_tail_hits(shape, a, aim) for a in avoid):
            return shape
    return first


_EDGE_NORMAL = {"bottom": (0, 1), "top": (0, -1), "left": (-1, 0), "right": (1, 0)}


def _tail_candidates(rect, face, bubble_type, sizes, bounds):
    """Every tail :func:`_tail_shape` may draw, most preferred first."""
    x0, y0, x1, y1 = rect
    fcx, fcy = (face[0] + face[2]) / 2, (face[1] + face[3]) / 2
    past = {"bottom": fcy - y1, "top": y0 - fcy, "left": x0 - fcx, "right": fcx - x1}
    # Edges the face lies beyond, furthest first; then, to route round a
    # neighbour, the other edges on the face's side of the bubble's centre.
    toward = {"bottom": fcy > (y0 + y1) / 2, "top": fcy < (y0 + y1) / 2,
              "left": fcx < (x0 + x1) / 2, "right": fcx > (x0 + x1) / 2}
    edges = sorted((e for e in past if past[e] > 0), key=lambda e: -past[e])
    edges += sorted((e for e in past if past[e] <= 0 and toward[e]), key=lambda e: -past[e])
    if not edges:  # the face's centre is the bubble's: leave by the bottom
        edges = ["bottom"]
    for edge in edges:
        horizontal = edge in ("top", "bottom")
        a, b = (x0, x1) if horizontal else (y0, y1)
        # Off the corners: past a speech bubble's rounding, or within the
        # middle stretch where a cloud or starburst meets its box.
        inset = (0.3 * (b - a) if bubble_type in ("thought", "shout")
                 else min((b - a) / 2, 20 + sizes["base"] / 2))
        lo, hi = a + inset, b - inset
        want = min(hi, max(lo, fcx if horizontal else fcy))
        spots = sorted({want} | {lo + (hi - lo) * k / 6 for k in range(7)},
                       key=lambda s: abs(s - want))
        fixed = {"bottom": y1, "top": y0, "left": x0, "right": x1}[edge]
        for s in spots:
            root = (s, fixed) if horizontal else (fixed, s)
            yield _tail_at(rect, edge, root, face, bubble_type, sizes, bounds)


def _tail_at(rect, edge, root, face, bubble_type, sizes, bounds) -> dict[str, Any]:
    """The tail leaving ``edge`` at ``root`` toward ``face`` (see :func:`_tail_shape`)."""
    nx, ny = _EDGE_NORMAL[edge]
    rx, ry = root
    fcx, fcy = (face[0] + face[2]) / 2, (face[1] + face[3]) / 2
    d = math.hypot(fcx - rx, fcy - ry) or 1.0
    ux, uy = (fcx - rx) / d, (fcy - ry) / d
    if ux * nx + uy * ny < 0.5:
        # Never run along the bubble's own edge: at most 60° off its normal.
        tx, ty = ux - (ux * nx + uy * ny) * nx, uy - (ux * nx + uy * ny) * ny
        tn = math.hypot(tx, ty) or 1.0
        ux, uy = 0.5 * nx + 0.866 * tx / tn, 0.5 * ny + 0.866 * ty / tn
    thought = bubble_type == "thought"
    lo, hi = (sizes["trail_lo"], sizes["trail_hi"]) if thought else (sizes["lo"], sizes["hi"])
    length = max(lo, min(hi, _ray_to_box((rx, ry), (ux, uy), face) - _TAIL_FACE_GAP))
    if bounds is not None:  # keep the tip inside the panel
        W, H = bounds
        for v, u, top in ((rx, ux, W), (ry, uy, H)):
            if u > 0:
                length = min(length, (top - 2 - v) / u)
            elif u < 0:
                length = min(length, (v - 2) / -u)
        length = max(length, 1.0)

    if thought:
        r1 = sizes["dot"]
        radii = [r1, max(2, round(r1 * 0.7)), max(2, round(r1 * 0.45))]
        first, last = 3 + radii[0], max(3 + radii[0], length - radii[2])
        dots = []
        for k, r in enumerate(radii):
            s = first + (last - first) * k / 2
            dots.append([round(rx + ux * s), round(ry + uy * s), r])
        return {"root": [round(rx), round(ry)], "dots": dots}

    half = sizes["base"] / 2
    tip = (rx + ux * length, ry + uy * length)
    if bubble_type == "shout":
        # A spike out of the starburst's body: based inside it, at right
        # angles to the spike, so the fill joins the two.
        x0, y0, x1, y1 = rect
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        bx, by = cx + (rx - cx) * 0.65, cy + (ry - cy) * 0.65
        px, py = -uy * half, ux * half
        base = [(bx + px, by + py), (bx - px, by - py)]
    else:
        # Along the edge, sunk past the outline so the fill hides it there.
        sink = _BUBBLE_BORDER_WIDTH.get(bubble_type, 2) + 1
        bx, by = rx - nx * sink, ry - ny * sink
        base = ([(bx - half, by), (bx + half, by)] if edge in ("top", "bottom")
                else [(bx, by - half), (bx, by + half)])
    return {"points": [[round(px), round(py)] for px, py in base + [tip]]}


def _ray_to_box(origin: tuple[float, float], direction: tuple[float, float],
                box: tuple[int, int, int, int]) -> float:
    """Distance along the ray to where it enters ``box``; if it misses, the
    distance from ``origin`` to the box (0 when inside it)."""
    (ox, oy), (dx, dy) = origin, direction
    t0, t1 = 0.0, math.inf
    for o, d, lo, hi in ((ox, dx, box[0], box[2]), (oy, dy, box[1], box[3])):
        if abs(d) < 1e-9:
            if not lo <= o <= hi:
                t0, t1 = math.inf, -math.inf
            continue
        a, b = (lo - o) / d, (hi - o) / d
        t0, t1 = max(t0, min(a, b)), min(t1, max(a, b))
    if t0 <= t1:
        return t0
    px, py = min(max(ox, box[0]), box[2]), min(max(oy, box[1]), box[3])
    return math.hypot(px - ox, py - oy)


def _tail_hits(shape: dict[str, Any], rect, aim=None) -> bool:
    """Whether a tail (see :func:`_tail_shape`) runs into ``rect`` or, given
    the point it ``aim``s at, points through it on the way there."""
    return shape_hits_rect(shape, rect, aim)


def _draw_tail(draw: ImageDraw.ImageDraw, shape: dict[str, Any], bubble_type: str) -> None:
    """Draw a :func:`_tail_shape` tail: white-filled, outlined to match its bubble.

    speech  -> triangle, solid outline
    whisper -> triangle, dashed outline
    shout   -> spike, the starburst's heavy outline
    thought -> three shrinking dots trailing toward the speaker
    """
    border = _BUBBLE_BORDER_WIDTH.get(bubble_type, 2)
    if "dots" in shape:
        for px, py, r in shape["dots"]:
            draw.ellipse([px - r, py - r, px + r, py + r],
                         fill="#ffffff", outline="#000000", width=border)
        return
    p1, p2, tip = (tuple(p) for p in shape["points"])
    draw.polygon([p1, p2, tip], fill="#ffffff")
    for p in (p1, p2):
        if bubble_type == "whisper":
            _dashed_line(draw, p, tip, border)
        else:
            draw.line([p, tip], fill="#000000", width=border)


def _draw_link(draw: ImageDraw.ImageDraw, upper: tuple[int, int, int, int],
               lower: tuple[int, int, int, int], bubble_type: str, width: int) -> None:
    """Join a speaker's consecutive balloons: a short white bridge from the
    bottom of ``upper`` to the top of ``lower`` (two dots between clouds).

    Where the two overlap horizontally the bridge is upright, in the middle
    of the overlap; otherwise it slants between their nearest ends.
    """
    border = _BUBBLE_BORDER_WIDTH.get(bubble_type, 2)

    def middle(r):  # where a bridge may meet the balloon: clear of its rounded corners
        inset = min(20 + width / 2, (r[2] - r[0]) / 2)
        return r[0] + inset, r[2] - inset

    (ulo, uhi), (llo, lhi) = middle(upper), middle(lower)
    lo, hi = max(ulo, llo), min(uhi, lhi)
    if lo <= hi:
        ax = bx = (lo + hi) / 2
    else:
        ax = min(max((llo + lhi) / 2, ulo), uhi)
        bx = min(max(ax, llo), lhi)
    ay, by = upper[3], lower[1]
    if bubble_type == "thought":
        r = max(3, width // 3)
        for f in (1 / 3, 2 / 3):
            px, py = ax + (bx - ax) * f, ay + (by - ay) * f
            draw.ellipse([px - r, py - r, px + r, py + r], fill="#ffffff", outline="#000000", width=border)
        return
    sink = border + 1
    half = width / 2
    left = [(ax - half, ay - sink), (bx - half, by + sink)]
    right = [(ax + half, ay - sink), (bx + half, by + sink)]
    draw.polygon(left + right[::-1], fill="#ffffff")
    for side in (left, right):
        if bubble_type == "whisper":
            _dashed_line(draw, side[0], side[1], border)
        else:
            draw.line([(side[0][0], side[0][1] + sink), (side[1][0], side[1][1] - sink)],
                      fill="#000000", width=border)


def _dashed_line(draw: ImageDraw.ImageDraw, a, b, width: int, dash: int = 10, gap: int = 6) -> None:
    """A dashed black line from ``a`` to ``b`` (the whisper border's rhythm)."""
    length = math.dist(a, b)
    s = 0.0
    while s < length:
        e = min(s + dash, length)
        draw.line([(a[0] + (b[0] - a[0]) * s / length, a[1] + (b[1] - a[1]) * s / length),
                   (a[0] + (b[0] - a[0]) * e / length, a[1] + (b[1] - a[1]) * e / length)],
                  fill="#000000", width=width)
        s = e + gap


# ---------------------------------------------------------------------------
# Drawing at the upscaled size
# ---------------------------------------------------------------------------


def _draw_for(img: Image.Image, canvas: Image.Image | None):
    """The draw target for ``img``: itself, or teed onto the upscaled ``canvas``."""
    return ImageDraw.Draw(img) if canvas is None else _TeeDraw(img, canvas)


class _TeeDraw:
    """An ``ImageDraw`` on the panel that replays every shape, scaled, on
    its upscaled copy.

    The layout never sees the canvas: text is measured on the panel, and the
    panel is drawn on exactly as it would be alone (so later steps read the
    same busyness). Each shape is drawn again on the canvas, ``k`` times the
    panel's size, with its coordinates, line widths, radii and font size
    multiplied by ``k``: the same lettering, rasterised at full size.
    """

    def __init__(self, img: Image.Image, canvas: Image.Image):
        self._small = ImageDraw.Draw(img)
        self._big = ImageDraw.Draw(canvas)
        self._k = canvas.width / img.width
        self._fonts: dict[int, Any] = {}

    def textbbox(self, xy, text, **kw):
        return self._small.textbbox(xy, text, **kw)

    def text(self, xy, text, **kw):
        self._small.text(xy, text, **kw)
        self._big.text(self._xy(xy), text, **self._scaled(kw))

    def line(self, xy, **kw):
        self._small.line(xy, **kw)
        self._big.line(self._xy(xy), **self._scaled(kw, width=1))

    def rectangle(self, xy, **kw):
        self._small.rectangle(xy, **kw)
        self._big.rectangle(self._xy(xy), **self._scaled(kw, width=1))

    def rounded_rectangle(self, xy, **kw):
        self._small.rounded_rectangle(xy, **kw)
        self._big.rounded_rectangle(self._xy(xy), **self._scaled(kw, width=1))

    def ellipse(self, xy, **kw):
        self._small.ellipse(xy, **kw)
        self._big.ellipse(self._xy(xy), **self._scaled(kw, width=1))

    def polygon(self, xy, **kw):
        self._small.polygon(xy, **kw)
        self._big.polygon(self._xy(xy), **self._scaled(kw, width=1))

    def _xy(self, xy):
        """Scale ``(x, y)``, ``[(x, y), ...]`` or a flat ``[x0, y0, x1, y1]``."""
        k = self._k
        if isinstance(xy[0], (tuple, list)):
            return [(x * k, y * k) for x, y in xy]
        return tuple(v * k for v in xy)

    def _scaled(self, kw: dict[str, Any], width: int | None = None) -> dict[str, Any]:
        """Draw kwargs at canvas scale; ``width`` is the call's default line width."""
        k = self._k
        out = dict(kw)
        if width is not None:
            out["width"] = max(1, round(kw.get("width", width) * k))
        for key in ("radius", "stroke_width"):
            if key in kw:
                out[key] = round(kw[key] * k)
        if "font" in kw:
            out["font"] = self._font(kw["font"])
        return out

    def _font(self, font):
        # Keyed by id; the entry holds the font too, so the id isn't reused.
        if id(font) not in self._fonts:
            # A bitmap fallback font can't be resized; it stays small.
            variant = getattr(font, "font_variant", None)
            self._fonts[id(font)] = (font, variant(size=font.size * self._k) if variant else font)
        return self._fonts[id(font)][1]


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


def _line_height(font) -> int:
    """Distance between successive lines: the font's ascent + descent.

    Not a line's own ink height — a line with no ascenders or descenders
    ("are more") is short, and advancing by it made the next line ride up
    into it.
    """
    try:
        ascent, descent = font.getmetrics()
        return ascent + descent
    except AttributeError:  # bitmap fallback font
        bbox = font.getbbox("Ay")
        return bbox[3] - bbox[1]


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
    """Return ``(x0, y0, x1, y1)`` for every face on ``img``.

    Delegates to the hybrid YuNet + MediaPipe detector in
    ``lazycomics.face_detector`` — empirically YuNet catches the
    medium-distance stylised comic faces MediaPipe misses, and MediaPipe
    catches the giant-closeup case YuNet misses. Module-level so tests
    can monkeypatch.
    """
    return [(f.x0, f.y0, f.x1, f.y1) for f in face_detector.detect_faces(img)]
