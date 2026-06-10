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

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from lazycomics import face_detector
from lazycomics.config import cfg_get, load_config
from lazycomics.models import Project

__all__ = ["render_text"]


# ---------------------------------------------------------------------------
# Tunable defaults (three-tier: arg → config "text.<key>" → these)
# ---------------------------------------------------------------------------
#
# Font sizes are expressed as a fraction of the panel's *width*, then scaled
# by ``font_scale`` and floored at ``min_font_px``. Width-relative keeps text
# legible across the wildly different panel sizes a comic produces (a 576px
# grid cell vs a 1184px splash) without per-panel tuning. Bump ``font_scale``
# to enlarge everything at once, or set a per-type ratio for finer control.
_DEFAULT_DIALOGUE_FONT_RATIO = 0.030   # ~31px on a 1024px-wide panel
_DEFAULT_CAPTION_FONT_RATIO = 0.028    # captions a touch smaller than dialogue
_DEFAULT_SFX_FONT_RATIO = 0.050        # SFX stay punchy/large
_DEFAULT_MIN_FONT_PX = 16              # never drop below this, even on tiny panels
_DEFAULT_FONT_SCALE = 1.0              # global multiplier on every text size
# Tail length as a fraction of the panel's *short* edge, floored at 16px.
# Real comic tails are short stubs from the bubble toward the speaker, not
# leader lines that traverse the panel.
_DEFAULT_TAIL_FRAC = 0.05              # ~38px on a 768px-tall panel
_TAIL_MIN_PX = 16


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
        img = Image.open(panel_path).convert("RGBA")
        img = _render_one(img, panel_data, text_cfg)
        img.convert("RGB").save(out_path)
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


def _font_px(ratio: float, panel_w: int, text_cfg: dict[str, float]) -> int:
    """Panel-width-relative font size, scaled and floored per config."""
    return max(int(text_cfg["min_font"]),
               int(panel_w * ratio * text_cfg["font_scale"]))


# ---------------------------------------------------------------------------
# Per-panel composition (order: captions, SFX, dialogue on top)
# ---------------------------------------------------------------------------


def _render_one(img: Image.Image, panel: dict[str, Any],
                text_cfg: dict[str, float]) -> Image.Image:
    img = _render_captions(img, panel.get("caption_boxes") or [], text_cfg)
    img = _render_sfx(img, panel.get("sfx_lines") or [], text_cfg)
    img = _render_dialogue(
        img,
        panel.get("dialogue_lines") or [],
        panel.get("bubble_layout") or [],
        text_cfg,
    )
    return img


def _render_captions(img: Image.Image, captions: list[dict[str, Any]],
                     text_cfg: dict[str, float]) -> Image.Image:
    if not captions:
        return img
    draw = ImageDraw.Draw(img)
    W, H = img.size
    font = _load_font(size=_font_px(text_cfg["caption_ratio"], W, text_cfg))
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


def _render_sfx(img: Image.Image, sfx_lines: list[dict[str, Any]],
                text_cfg: dict[str, float]) -> Image.Image:
    if not sfx_lines:
        return img
    draw = ImageDraw.Draw(img)
    W, H = img.size
    font = _load_font(size=_font_px(text_cfg["sfx_ratio"], W, text_cfg))
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
    text_cfg: dict[str, float],
) -> Image.Image:
    """Place bubbles in their speaker's ``bubble_layout`` region.

    Each speaker gets a region (from ``bubble_layout`` or an auto-split)
    and their lines stack within it.

    NOTE: this preserves *per-speaker* stacking but does not yet enforce a
    single global reading order across speakers — reading-order-correct,
    negative-space-aware placement is tracked as separate work. What this
    function does guarantee: bubble shapes are sized so their text is fully
    contained (clouds and starbursts are drawn larger than their text box),
    and tails are short stubs toward the speaker rather than panel-spanning
    leader lines.

    TODO (Phase 2): prefer ``actual_character_regions`` over the predicted
    ``bubble_layout`` when the generator writes back where it actually
    painted each character.
    """
    if not dialogue:
        return img

    draw = ImageDraw.Draw(img)
    W, H = img.size
    font = _load_font(size=_font_px(text_cfg["dialogue_ratio"], W, text_cfg))
    margin = 20
    padding = 12
    max_tail = max(_TAIL_MIN_PX, int(min(W, H) * text_cfg["tail_frac"]))

    faces = _detect_all_faces(img)

    # Speaker order = order of first mention in CBML.
    speaker_order: list[str] = []
    for line in dialogue:
        speaker = (line.get("character") or "").strip()
        if speaker and speaker not in speaker_order:
            speaker_order.append(speaker)

    # The enricher's bubble_layout is a *prediction* (equal horizontal
    # slices in character order). We use it only as a prior for the
    # left-to-right ordering of named characters; the real positions come
    # from the faces detected on the actual generated panel.
    region_by_speaker = _pixel_regions(bubble_layout, W, H)
    _fill_missing_regions(region_by_speaker, speaker_order, W, H)

    # Anchor each speaker to where they actually are: their detected face
    # when we can associate one, else the centre of their predicted slice.
    anchors = _resolve_speaker_anchors(speaker_order, region_by_speaker, faces, W, H)

    # Pre-compute a coarse "busy-ness" map so placement can prefer empty
    # negative space (sky, walls) over detailed regions (faces, foreground).
    busy = _busyness_map(img)

    max_bubble_width = int(W * 0.42)
    placed: list[tuple[int, int, int, int]] = []
    prev_rect: tuple[int, int, int, int] | None = None
    prev_speaker: str | None = None

    for line in dialogue:
        speaker = (line.get("character") or "").strip()
        text = (line.get("text") or "").strip()
        if not text or speaker not in anchors:
            continue
        bubble_type = line.get("bubble_type") or "speech"
        ax, ay, has_face = anchors[speaker]

        # Cloud (thought) and starburst (shout) need their text box smaller
        # than the drawn shape or the outline clips the glyphs. We wrap to
        # the shape's *interior* width, then inflate the footprint back out
        # by the same fraction. Speech/whisper fill their rect (interior 1.0).
        wf, hf = _BUBBLE_INTERIOR.get(bubble_type, (1.0, 1.0))
        wrap_width = max(20, int((max_bubble_width - 2 * padding) * wf))
        wrapped = _wrap_text(text, font, wrap_width, draw)
        line_sizes = [_measure(draw, l, font) for l in wrapped]
        text_w = max(w for w, _ in line_sizes)
        text_h = sum(h for _, h in line_sizes)

        bubble_w = int((text_w + 2 * padding) / wf)
        bubble_h = int((text_h + 2 * padding) / hf)
        bubble_w = min(bubble_w, W - 2 * margin)
        bubble_h = min(bubble_h, H - 2 * margin)

        # Consecutive lines from the same speaker stack vertically (the
        # classic convention for one character's run of dialogue).
        same_speaker = speaker == prev_speaker
        bubble_x, bubble_y = _place_bubble(
            bubble_w, bubble_h, (ax, ay), placed, faces, busy,
            prev_rect, same_speaker, W, H, margin,
        )
        rect = (bubble_x, bubble_y, bubble_x + bubble_w, bubble_y + bubble_h)

        _draw_bubble(draw, bubble_x, bubble_y, bubble_w, bubble_h, bubble_type)

        # Only draw a tail when we actually located the speaker (a detected
        # face). With no face the anchor is a guess, so a tail would point
        # at an arbitrary spot — we omit it rather than mislead.
        if has_face:
            _draw_tail(draw, rect, (ax, ay), bubble_type, max_tail)

        # Render the text last so it sits on top of bubble + tail fills.
        # Speech/whisper: left-aligned at the padding inset. Thought/shout:
        # centred inside the inflated shape, clear of the cloud / spikes.
        centred = bubble_type in ("thought", "shout")
        y_text = (bubble_y + (bubble_h - text_h) // 2) if centred else (bubble_y + padding)
        for l, (lw, lh) in zip(wrapped, line_sizes):
            x_text = (bubble_x + (bubble_w - lw) // 2) if centred else (bubble_x + padding)
            draw.text((x_text, y_text), l, fill="#000000", font=font)
            y_text += lh

        placed.append(rect)
        prev_rect = rect
        prev_speaker = speaker

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


def _resolve_speaker_anchors(
    speakers: list[str],
    region_by_speaker: dict[str, tuple[int, int, int, int]],
    faces: list[tuple[int, int, int, int]],
    W: int, H: int,
) -> dict[str, tuple[int, int, bool]]:
    """Map each speaker to an anchor point ``(x, y, has_face)``.

    The anchor is where the speaker *is* — the point a tail should aim at
    and the spot a bubble should sit near. We get it by associating
    detected faces to speakers using the enricher's predicted left-to-right
    character order as a prior: faces are sorted by x, speakers are sorted
    by their predicted slice centre, and the two are matched positionally
    (leftmost predicted speaker ↔ leftmost face, and so on). Speakers left
    over when there are fewer faces than speakers fall back to the centre
    of their predicted slice.

    This is deliberately position-aware rather than order-aware: it uses
    where characters actually rendered, not the arbitrary CBML authoring
    order, so a bubble lands next to the right character even when the
    generator placed them on the opposite side from the script's ordering.
    """
    def pred_centre(s: str) -> tuple[int, int]:
        rx, ry, rw, rh = region_by_speaker.get(s, (0, 0, W, H))
        return (rx + rw // 2, ry + rh // 2)

    anchors: dict[str, tuple[int, int, bool]] = {}
    if faces:
        faces_by_x = sorted(faces, key=lambda f: (f[0] + f[2]) / 2)
        speakers_by_x = sorted(speakers, key=lambda s: pred_centre(s)[0])
        for i, s in enumerate(speakers_by_x):
            if i < len(faces_by_x):
                fx0, fy0, fx1, fy1 = faces_by_x[i]
                anchors[s] = ((fx0 + fx1) // 2, (fy0 + fy1) // 2, True)
            else:
                px, py = pred_centre(s)
                anchors[s] = (px, py, False)
    else:
        for s in speakers:
            px, py = pred_centre(s)
            anchors[s] = (px, py, False)
    return anchors


def _busyness_map(img: Image.Image, grid_w: int = 40):
    """A coarse map of visual detail, for steering bubbles to empty space.

    Returns ``(small_grayscale_edges, grid_w, grid_h)``. Each cell holds
    the average edge energy of the corresponding panel region (0 = flat
    background, high = busy foreground/faces). Computed with an edge filter
    then box-downsampled, so it costs one filter pass regardless of how
    many bubbles get placed. On a flat test image it is uniformly zero, so
    placement stays deterministic.
    """
    grid_h = max(1, int(grid_w * img.height / max(1, img.width)))
    try:
        edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
        small = edges.resize((grid_w, grid_h), resample=Image.BOX)
    except Exception:
        small = Image.new("L", (grid_w, grid_h), 0)
    return small, grid_w, grid_h


def _busy_in_rect(busy, W: int, H: int,
                  rect: tuple[int, int, int, int]) -> float:
    """Mean edge energy (0..1) of the busyness map under ``rect``."""
    small, gw, gh = busy
    x0, y0, x1, y1 = rect
    cx0 = max(0, min(gw - 1, int(x0 / max(1, W) * gw)))
    cx1 = max(0, min(gw - 1, int(x1 / max(1, W) * gw)))
    cy0 = max(0, min(gh - 1, int(y0 / max(1, H) * gh)))
    cy1 = max(0, min(gh - 1, int(y1 / max(1, H) * gh)))
    total = 0
    n = 0
    for cy in range(cy0, cy1 + 1):
        for cx in range(cx0, cx1 + 1):
            total += small.getpixel((cx, cy))
            n += 1
    return (total / n) / 255.0 if n else 0.0


def _overlap_area(a: tuple[int, int, int, int],
                  b: tuple[int, int, int, int]) -> int:
    """Area of the intersection of two (x0, y0, x1, y1) rects (0 if none)."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy


def _place_bubble(
    bw: int, bh: int,
    anchor: tuple[int, int],
    placed: list[tuple[int, int, int, int]],
    faces: list[tuple[int, int, int, int]],
    busy,
    prev_rect: tuple[int, int, int, int] | None,
    same_speaker: bool,
    W: int, H: int, margin: int,
) -> tuple[int, int]:
    """Choose a top-left for a ``bw×bh`` bubble near ``anchor``.

    Searches a set of candidate slots (stacking up/down in three columns
    around the anchor) and scores each by a weighted cost:

    * hard penalty for overlapping an already-placed bubble (bubbles must
      not collide),
    * penalty for covering a detected face (keep faces visible),
    * penalty for sitting over busy/detailed regions (prefer negative space),
    * penalty proportional to tail length (keep the bubble near its speaker),
    * a reading-order bias so a later bubble tends to sit below / to the
      right of the previous one (Western top-to-bottom, left-to-right flow).

    As a special case, consecutive lines from the *same* speaker stack
    directly beneath the previous bubble when there's room — the usual
    convention for one character's run of dialogue.

    Returns the clamped, in-bounds top-left. The cost trades these off
    rather than hard-forbidding any one, so placement degrades gracefully
    on cramped panels instead of failing.
    """
    ax, ay = anchor

    # Same-speaker run: stack straight down under the previous bubble when
    # that slot is free of other bubbles and faces.
    if same_speaker and prev_rect is not None:
        sp_cx = (prev_rect[0] + prev_rect[2]) // 2
        x0 = max(margin, min(W - margin - bw, sp_cx - bw // 2))
        y0 = prev_rect[3] + 14
        rect = (x0, y0, x0 + bw, y0 + bh)
        if (y0 + bh <= H - margin
                and all(_overlap_area(rect, p) == 0 for p in placed)
                and all(_overlap_area(rect, f) == 0 for f in faces)):
            return x0, y0

    head_gap = max(18, bh // 3)

    # Candidate bubble-centre columns: above the head, and offset left/right.
    columns = [ax, ax - (bw // 2 + 24), ax + (bw // 2 + 24)]
    # Stack slots upward from just above the head, plus a few below as
    # fallback when there's no room above.
    centres: list[tuple[int, int]] = []
    top_base = ay - head_gap - bh // 2
    for col in columns:
        for k in range(0, 6):
            centres.append((col, top_base - k * (bh + 14)))
        for k in range(0, 3):
            centres.append((col, ay + head_gap + bh // 2 + k * (bh + 14)))

    def clamp(cx: int, cy: int) -> tuple[int, int]:
        x = max(margin, min(W - margin - bw, cx - bw // 2))
        y = max(margin, min(H - margin - bh, cy - bh // 2))
        return x, y

    best: tuple[int, int] | None = None
    best_cost = float("inf")
    area = max(1, bw * bh)

    for cx, cy in centres:
        x0, y0 = clamp(cx, cy)
        rect = (x0, y0, x0 + bw, y0 + bh)

        cost = 0.0
        # Bubble–bubble overlap: near-absolute avoidance.
        for p in placed:
            cost += _overlap_area(rect, p) * 6.0
        # Face coverage: keep characters' faces readable.
        for f in faces:
            cost += _overlap_area(rect, f) * 1.5
        # Negative-space preference (flat regions are cheap to cover).
        cost += _busy_in_rect(busy, W, H, rect) * area * 1.2
        # Keep the bubble near the speaker → short tail. Distance from the
        # bubble's bottom-centre (where the tail roots) to the anchor.
        root = (x0 + bw // 2, y0 + bh)
        cost += math.hypot(root[0] - ax, root[1] - ay) * 1.0
        # Reading-order bias relative to the previous bubble.
        if prev_rect is not None:
            if y0 < prev_rect[1] - 12:               # clearly higher → backwards
                cost += (prev_rect[1] - y0) * 2.5
            elif abs(y0 - prev_rect[1]) <= bh and x0 < prev_rect[0] - 12:
                cost += (prev_rect[0] - x0) * 1.2     # same band but to the left

        if cost < best_cost:
            best_cost = cost
            best = (x0, y0)

    # centres is never empty, so best is always set; fall back defensively.
    return best if best is not None else clamp(ax, ay - head_gap - bh // 2)


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
               max_tail_px: int) -> None:
    """Draw a tail/leader from ``bubble_rect`` toward ``target``.

    speech  -> triangular tail
    thought -> trailing dots (small circles between bubble and target)
    shout, whisper -> no tail (the shape itself carries the emphasis)

    ``max_tail_px`` caps the tail length. Comic tails are short stubs that
    merely indicate the speaker's direction; they should not stretch across
    the panel to physically touch the face. The tail therefore points
    *toward* the target but stops at ``max_tail_px``.
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

    # Cap the tail to a short stub pointing toward the target.
    dx = tx - bcx
    dy = ty - origin_y
    distance = math.hypot(dx, dy)
    if distance > max_tail_px and distance > 0:
        scale = max_tail_px / distance
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
    """Return ``(x0, y0, x1, y1)`` for every face on ``img``.

    Delegates to the hybrid YuNet + MediaPipe detector in
    ``lazycomics.face_detector`` — empirically YuNet catches the
    medium-distance stylised comic faces MediaPipe misses, and MediaPipe
    catches the giant-closeup case YuNet misses. Module-level so tests
    can monkeypatch.
    """
    return [(f.x0, f.y0, f.x1, f.y1) for f in face_detector.detect_faces(img)]
