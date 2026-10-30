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
  thought      cloud outline (overlapping ellipses) + trailing dots
  shout        starburst polygon (12-point star); no tail
  whisper      rectangle with dashed border; no tail
  ============ ====================================================

Face-aware placement: ``_detect_all_faces`` returns all face bboxes (hybrid
YuNet + MediaPipe). Panel characters are matched to faces using the
enricher's predicted left-to-right order as a prior (see
``_match_characters_to_faces``), nudged on multi-character panels by face
identity against each character's reference image (``face_identity``,
OpenCV SFace — downloaded on first use). A speaker with no confident match
gets no tail rather than a wrong one. Best-effort caveat: identity is weak
on comic art, so it only overrules position when the evidence is clear; a
mirrored panel with weak evidence still gets mirrored tails.

Bubbles are then laid out together (``_layout_bubbles``) so they read in
CBML order by construction, without overlapping each other or the
captions/SFX.

The output PNG may be reviewed and hand-overwritten; the renderer skips
panels whose output already exists unless ``force=True``.

Alongside each output PNG it writes ``panels_text/<panel_id>.layout.json``:
what it placed and why (detected faces, the character→face matches, each
bubble's rect / tail anchor in CBML line order, caption and SFX boxes).
``lazycomics.review`` reads it to check reading order and overlaps without
re-deriving the layout.

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

from lazycomics import face_detector, face_identity
from lazycomics.config import cfg_get, load_config
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
# Tail length as a fraction of the panel's *short* edge, floored at 16px.
# Real comic tails are short stubs from the bubble toward the speaker, not
# leader lines that traverse the panel.
_DEFAULT_TAIL_FRAC = 0.05              # ~38px on a 768px-tall panel
_TAIL_MIN_PX = 16
# Shortest tail ever drawn: a speaker right under the bubble still gets a
# visible stub, not a 2px sliver (old plan §14.8d).
_TAIL_STUB_PX = 12


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
        layout: dict[str, Any] = {"size": list(img.size)}
        img = _render_one(img, panel_data, text_cfg, record=layout, known=known)
        img.convert("RGB").save(out_path)
        out_path.with_suffix(".layout.json").write_text(
            json.dumps(layout, indent=2), encoding="utf-8",
        )
        if "faces" in layout:  # dialogue was laid out: persist what was decided
            panel_data["detected_faces"] = layout["faces"]
            panel_data["actual_character_regions"] = layout["regions"]
            panel_data["detected_faces_for"] = panel_hash
            enriched_path.write_text(
                json.dumps(panel_data, indent=2, ensure_ascii=False), encoding="utf-8",
            )
        results[panel_id] = out_path

    return results


def _resolve_text_cfg(config: dict[str, Any]) -> dict[str, float]:
    """Resolve the ``text.*`` knobs into a flat dict, with defaults."""
    return {
        "dialogue_ratio": cfg_get(config, "text.dialogue_font_ratio", _DEFAULT_DIALOGUE_FONT_RATIO),
        "caption_ratio": cfg_get(config, "text.caption_font_ratio", _DEFAULT_CAPTION_FONT_RATIO),
        "sfx_ratio": cfg_get(config, "text.sfx_font_ratio", _DEFAULT_SFX_FONT_RATIO),
        "min_font": cfg_get(config, "text.min_font_px", _DEFAULT_MIN_FONT_PX),
        "font_scale": cfg_get(config, "text.font_scale", _DEFAULT_FONT_SCALE),
        "tail_frac": cfg_get(config, "text.tail_frac", _DEFAULT_TAIL_FRAC),
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
                known: dict[str, Any] | None = None) -> Image.Image:
    # Captions and SFX are placed first; their boxes are obstacles the
    # dialogue layout keeps clear of, so record them even if the caller
    # didn't ask for a record.
    rec = record if record is not None else {}
    img = _render_captions(img, panel.get("caption_boxes") or [], text_cfg, rec)
    img = _render_sfx(img, panel.get("sfx_lines") or [], text_cfg, rec)
    img = _render_dialogue(
        img,
        panel.get("dialogue_lines") or [],
        panel.get("bubble_layout") or [],
        text_cfg,
        rec,
        obstacles=rec.get("captions", []) + rec.get("sfx", []),
        known=known,
        refs={c["identifier"]: c["reference_images"][0]
              for c in panel.get("characters") or [] if c.get("reference_images")},
    )
    return img


def _record(record: dict[str, Any] | None, key: str, item: Any) -> None:
    """Append ``item`` to ``record[key]`` when the caller wants a layout record."""
    if record is not None:
        record.setdefault(key, []).append(item)


def _render_captions(img: Image.Image, captions: list[dict[str, Any]],
                     text_cfg: dict[str, float],
                     record: dict[str, Any] | None = None) -> Image.Image:
    if not captions:
        return img
    draw = ImageDraw.Draw(img)
    W, H = img.size
    font = _load_font(size=_font_px(text_cfg["caption_ratio"], W, H, text_cfg))
    line_h = _line_height(font)
    margin = 20
    padding = 10
    max_text_width = max(20, _wrap_width(_CAPTION_MAX_WIDTH_FRAC, W, H, margin) - 2 * padding)

    for cap in captions:
        text = (cap.get("text") or "").strip()
        if not text:
            continue
        bg = cap.get("bg_color") or "#000000"
        tc = cap.get("text_color") or "#ffffff"
        pos = cap.get("position") or "top-left"

        lines = _wrap_text(text, font, max_text_width, draw)
        text_w = max(_measure(draw, line, font)[0] for line in lines)
        text_h = line_h * len(lines)

        box_w = text_w + 2 * padding
        box_h = text_h + 2 * padding
        x, y = _anchor_to_xy(pos, W, H, box_w, box_h, margin)

        draw.rectangle([x, y, x + box_w, y + box_h], fill=bg)
        _record(record, "captions", [x, y, x + box_w, y + box_h])

        y_cursor = y + padding
        for line in lines:
            draw.text((x + padding, y_cursor), line, fill=tc, font=font)
            y_cursor += line_h
    return img


def _render_sfx(img: Image.Image, sfx_lines: list[dict[str, Any]],
                text_cfg: dict[str, float],
                record: dict[str, Any] | None = None) -> Image.Image:
    if not sfx_lines:
        return img
    draw = ImageDraw.Draw(img)
    W, H = img.size
    font = _load_font(size=_font_px(text_cfg["sfx_ratio"], W, H, text_cfg))
    margin = 20

    for sfx in sfx_lines:
        text = (sfx.get("text") or "").strip()
        if not text:
            continue
        color = sfx.get("color") or "#000000"
        pos = sfx.get("position") or "center"

        text_w, text_h = _measure(draw, text, font)
        x, y = _anchor_to_xy(pos, W, H, text_w, text_h, margin, allow_center=True)
        # Ink sits below the draw origin by the font's top bearing; record
        # where the glyphs actually land (plus the 2px outline).
        ix0, iy0, ix1, iy1 = draw.textbbox((x, y), text, font=font)
        _record(record, "sfx", [ix0 - 2, iy0 - 2, ix1 + 2, iy1 + 2])

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
# Dialogue: who is where, then a reading-order-correct layout
# ---------------------------------------------------------------------------


# Faces shorter than this fraction of the tallest face are treated as
# background extras and never matched to a named character.
_MIN_FACE_FRAC = 0.5
# Distance matching: a face whose distance to another character's predicted
# centre is within this fraction of the panel width of its own match's is
# ambiguous, and that speaker gets no tail (fail closed).
_AMBIGUOUS_FRAC = 0.05
# Identity blend: score = SFace cosine - this x |face - predicted centre| / W.
# Chosen on the golden panels (keeping their correct matches needs > 0.125;
# fixing the mirrored page_4_panel_1 needs < 0.45), then checked on four
# held-out W1 panels: no correct match broken (Docs/PLAN.md W3.2).
_IDENTITY_POSITION_WEIGHT = 0.25
_BUBBLE_GAP = 14


def _render_dialogue(
    img: Image.Image,
    dialogue: list[dict[str, Any]],
    bubble_layout: list[dict[str, Any]],
    text_cfg: dict[str, float],
    record: dict[str, Any] | None = None,
    *,
    obstacles: list[list[int]] | None = None,
    known: dict[str, Any] | None = None,
    refs: dict[str, str] | None = None,
) -> Image.Image:
    """Lay out and draw the dialogue bubbles, in CBML reading order.

    1. **Who is where.** Each panel character is matched to a detected face
       (:func:`_match_characters_to_faces`) so tails point at the right
       speaker — by predicted position, nudged by face identity against
       each character's reference image (``refs``) when there are several
       characters; a speaker with no confident match gets no tail and a bubble
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
    stubs toward the speaker. Writes ``faces``, ``regions`` and one
    ``bubbles`` entry per drawn line into ``record``.
    """
    if not dialogue:
        return img

    draw = ImageDraw.Draw(img)
    W, H = img.size
    font = _load_font(size=_font_px(text_cfg["dialogue_ratio"], W, H, text_cfg))
    line_h = _line_height(font)
    margin = 20
    padding = 12
    max_tail = max(_TAIL_MIN_PX, int(min(W, H) * text_cfg["tail_frac"]))
    max_bubble_width = _wrap_width(_BUBBLE_MAX_WIDTH_FRAC, W, H, margin)
    known = known or {}

    if known.get("detected_faces") is not None:
        faces = [tuple(f) for f in known["detected_faces"]]
    else:
        faces = _detect_all_faces(img)

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
        identity = (_identity_similarity(img, faces, refs)
                    if refs and faces and len(characters) > 1 else None)
        regions = _match_characters_to_faces(characters, region_by_char, faces, W,
                                             similarity=identity)
        if record is not None:
            record["matching"] = "position+identity" if identity else "position"
    # A matched region is a face even if the detector missed it (a hand
    # correction for a missed face): keep bubbles off it like any other.
    faces = list(faces) + [r for r in regions.values() if r not in faces]

    if record is not None:
        record["faces"] = [list(f) for f in faces]
        record["regions"] = {c: list(r) for c, r in regions.items()}

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

        # Cloud (thought) and starburst (shout) need their text box smaller
        # than the drawn shape or the outline clips the glyphs. We wrap to
        # the shape's *interior* width, then inflate the footprint back out
        # by the same fraction. Speech/whisper fill their rect (interior 1.0).
        wf, hf = _BUBBLE_INTERIOR.get(bubble_type, (1.0, 1.0))
        wrap_width = max(20, int((max_bubble_width - 2 * padding) * wf))
        wrapped = _wrap_text(text, font, wrap_width, draw)
        text_w = max(_measure(draw, l, font)[0] for l in wrapped)
        text_h = line_h * len(wrapped)
        bubbles.append({
            "line": line_no, "speaker": speaker, "type": bubble_type,
            "lines": wrapped, "text_h": text_h,
            "w": min(int((text_w + 2 * padding) / wf), W - 2 * margin),
            "h": min(int((text_h + 2 * padding) / hf), H - 2 * margin),
            "anchor": anchor, "has_face": has_face, "face": face,
            "tailed": bubble_type in ("speech", "thought"),
        })

    positions = _layout_bubbles(bubbles, faces, obstacles or [], _busyness_map(img),
                                W, H, margin, clearance=max_tail)

    for b, (x, y) in zip(bubbles, positions):
        w, h = b["w"], b["h"]
        rect = (x, y, x + w, y + h)
        _draw_bubble(draw, x, y, w, h, b["type"])

        # Only draw a tail when we actually located the speaker (a matched
        # face). With no face the anchor is a guess, so a tail would point
        # at an arbitrary spot — we omit it rather than mislead.
        tail = b["has_face"] and _draw_tail(draw, rect, b["anchor"], b["type"], max_tail)
        _record(record, "bubbles", {
            "line": b["line"], "speaker": b["speaker"], "type": b["type"],
            "rect": list(rect), "anchor": list(b["anchor"]), "tail": tail,
        })

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
    similarity: dict[str, list[float | None]] | None = None,
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

    With ``similarity`` (face-identity evidence from ``face_identity``:
    character → cosine per face), the assignment instead maximises each
    pair's similarity minus ``_IDENTITY_POSITION_WEIGHT`` × its distance
    from the predicted centre (as a fraction of the width). Identity can
    then overrule a mirrored prediction, while position still wins when the
    identity signal is weak — as on comic art it usually is.

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

    if similarity and any(s is not None for sims in similarity.values() for s in sims):
        index = {f: i for i, f in reversed(list(enumerate(faces)))}

        def score(c, f):
            sim = (similarity.get(c) or [])[index[f]] if c in similarity else None
            return (sim or 0.0) - _IDENTITY_POSITION_WEIGHT * abs(face_x(f) - centre[c]) / W

        if len(characters) <= len(cands):
            options = ([*zip(characters, p)] for p in itertools.permutations(cands, len(characters)))
        else:
            options = ([*zip(p, cands)] for p in itertools.permutations(characters, len(cands)))
        return dict(max(options, key=lambda pairs: sum(score(c, f) for c, f in pairs)))

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


def _beyond_reach(face: tuple[int, int, int, int], x: int, y: int,
                  w: int, h: int, reach: float) -> float:
    """How far past ``reach`` a bubble's tail root sits from ``face`` (px).

    The tail roots at the centre of the bubble edge facing the speaker and is
    capped at tail length, so a bubble further away than that leaves a stub
    pointing at nothing. 0 when within reach, or when the face is level with
    the bubble (no vertical tail; that case is penalised separately).
    """
    fx0, fy0, fx1, fy1 = face
    cx = x + w / 2
    if fy0 >= y + h:        # face below: tail from the bottom edge
        dy = fy0 - (y + h)
    elif fy1 <= y:          # face above: tail from the top edge
        dy = y - fy1
    else:
        return 0.0
    dx = max(fx0 - cx, 0.0, cx - fx1)
    return max(0.0, math.hypot(dx, dy) - reach)


def _layout_bubbles(
    bubbles: list[dict[str, Any]],
    faces: list[tuple[int, int, int, int]],
    obstacles: list[list[int]],
    busy,
    W: int, H: int, margin: int,
    *,
    clearance: int = 0,
) -> list[tuple[int, int]]:
    """Place every bubble at once so the panel reads in CBML order.

    (a) **Bands.** Lines go into vertical bands, top to bottom in CBML
        order. A line may share the previous line's band, to its right, when
        a different speaker standing further right says it — the Z-path of
        an alternating exchange. A speaker's consecutive lines always stack.
        Every allowed sharing pattern is tried and the cheapest wins.
    (b) **Positions.** Each band is as tall as its tallest bubble and sits
        strictly below the previous one. Band offsets are chosen jointly
        (a DP over candidate heights) and each bubble's horizontal position
        within its band greedily, all by one cost: covering faces (each
        grown by ``clearance``, the tail length, so no tail stabs into a
        face), covering
        captions/SFX, sitting over busy detail, distance from the bubble's
        bottom-centre to its speaker, a tailed bubble sitting level with
        its speaker's face (no tail direction), and a tailed bubble sitting
        beyond tail reach of that face (the stub can't attribute the line).

    Reading order and bubble/bubble non-overlap hold by construction.
    Returns top-left ``(x, y)`` per bubble, in input order.
    """
    if not bubbles:
        return []
    gap = _BUBBLE_GAP
    usable_w = W - 2 * margin
    step = max(8, (H - 2 * margin) // 40)
    keep_out = [(f[0] - clearance, f[1] - clearance, f[2] + clearance, f[3] + clearance)
                for f in faces]

    def rect_cost(b: dict[str, Any], x: int, y: int) -> float:
        w, h = b["w"], b["h"]
        rect = (x, y, x + w, y + h)
        ax, ay = b["anchor"]
        cost = sum(_overlap_area(rect, f) for f in keep_out) * 1.5
        cost += sum(_overlap_area(rect, tuple(o)) for o in obstacles) * 6.0
        cost += _busy_in_rect(busy, W, H, rect) * w * h * 1.2
        cost += math.hypot(x + w / 2 - ax, y + h - ay)
        if b["tailed"] and b["has_face"] and y <= ay <= y + h:
            cost += W
        if b["tailed"] and b["face"]:
            cost += _beyond_reach(b["face"], x, y, w, h, clearance + gap) * w * 0.5
        return cost

    band_cache: dict[tuple[tuple[int, ...], int], tuple[float, list[int]]] = {}

    def band_cost(band: tuple[int, ...], y: int) -> tuple[float, list[int]]:
        """Cheapest left-to-right placement of ``band``'s bubbles at height ``y``."""
        key = (band, y)
        if key not in band_cache:
            total, xs, lo = 0.0, [], margin
            for k, i in enumerate(band):
                b = bubbles[i]
                rest = sum(bubbles[j]["w"] + gap for j in band[k + 1:])
                hi = max(lo, W - margin - b["w"] - rest)
                # Near the speaker: centred on them, or offset by up to half
                # a bubble + 24px either side (the old placer's three
                # columns, sampled finer). Clamped to the band's free span.
                centred = b["anchor"][0] - b["w"] // 2
                reach = b["w"] // 2 + 24
                cands = {min(hi, max(lo, centred + reach * n // 4)) for n in range(-4, 5)}
                x = min(cands, key=lambda c: rect_cost(b, c, y))
                total += rect_cost(b, x, y)
                xs.append(x)
                lo = x + b["w"] + gap
            band_cache[key] = (total, xs)
        return band_cache[key]

    def heights(band: tuple[int, ...]) -> int:
        return max(bubbles[i]["h"] for i in band)

    def y_candidates(band: tuple[int, ...], lo: int, hi: int) -> list[int]:
        """Heights near the band's speakers: up to six bubble-slots above the
        head, three below (the old placer's reach). Only if reading order
        rules all of those out does the band search the whole ``[lo, hi]``.
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
                ok = [p for p in prev if p[0] + hs[k - 1] + gap <= y]
                if ok:
                    best = min(ok, key=lambda p: p[1])
                    cur.append((y, best[1] + cost, best[2] + [(y, xs)]))
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
        _, bands, placed = best
        for band, (y, xs) in zip(bands, placed):
            for i, x in zip(band, xs):
                positions[i] = (x, y)
    else:
        # Too much text to stack in the panel: keep the order, stack from the
        # top as far as it goes, and let the bottom bands overlap.
        y = margin
        for i, b in enumerate(bubbles):
            _, xs = band_cost((i,), min(y, H - margin - b["h"]))
            positions[i] = (xs[0], min(y, H - margin - b["h"]))
            y += b["h"] + gap
    return [positions[i] for i in range(len(bubbles))]


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
    "thought": (0.64, 0.54),
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
               bubble_type: str,
               max_tail_px: int) -> bool:
    """Draw a tail/leader from ``bubble_rect`` toward ``target``.

    speech  -> triangular tail
    thought -> trailing dots (small circles between bubble and target)
    shout, whisper -> no tail (the shape itself carries the emphasis)

    ``max_tail_px`` caps the tail length. Comic tails are short stubs that
    merely indicate the speaker's direction; they should not stretch across
    the panel to physically touch the face. The tail therefore points
    *toward* the target but stops at ``max_tail_px``. A tail shorter than
    ``_TAIL_STUB_PX`` is lengthened to it, so it reads as a stub rather than
    a vanishing sliver.

    Returns whether a tail was drawn.
    """
    if bubble_type in ("shout", "whisper"):
        return False

    bx0, by0, bx1, by1 = bubble_rect
    bcx = (bx0 + bx1) // 2
    tx, ty = target

    if ty > by1:
        origin_y = by1
    elif ty < by0:
        origin_y = by0
    else:
        # Target horizontally alongside the bubble — no clean tail direction.
        return False

    # Cap the tail to a short stub pointing toward the target.
    dx = tx - bcx
    dy = ty - origin_y
    distance = math.hypot(dx, dy)
    if distance > max_tail_px:
        scale = max_tail_px / distance
        tx = int(bcx + dx * scale)
        ty = int(origin_y + dy * scale)
    elif distance < _TAIL_STUB_PX:
        scale = _TAIL_STUB_PX / distance  # distance > 0: target is off the edge
        tx = int(bcx + dx * scale)
        ty = int(origin_y + dy * scale)

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
        return True

    # speech: filled triangle that overwrites the bubble outline at its base.
    tail_base = 14
    p1 = (bcx - tail_base // 2, origin_y)
    p2 = (bcx + tail_base // 2, origin_y)
    p3 = (tx, ty)
    draw.polygon([p1, p2, p3], fill="#ffffff")
    draw.line([p1, p3], fill="#000000", width=border)
    draw.line([p2, p3], fill="#000000", width=border)
    return True


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


def _identity_similarity(
    img: Image.Image,
    faces: list[tuple[int, int, int, int]],
    refs: dict[str, str],
) -> dict[str, list[float | None]] | None:
    """Per-character similarity to each face (``face_identity``; may download
    its model on first use). Module-level so tests can monkeypatch.
    """
    return face_identity.similarity(img, faces, refs)


def _detect_all_faces(img: Image.Image) -> list[tuple[int, int, int, int]]:
    """Return ``(x0, y0, x1, y1)`` for every face on ``img``.

    Delegates to the hybrid YuNet + MediaPipe detector in
    ``lazycomics.face_detector`` — empirically YuNet catches the
    medium-distance stylised comic faces MediaPipe misses, and MediaPipe
    catches the giant-closeup case YuNet misses. Module-level so tests
    can monkeypatch.
    """
    return [(f.x0, f.y0, f.x1, f.y1) for f in face_detector.detect_faces(img)]
