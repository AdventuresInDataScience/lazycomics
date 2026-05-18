"""Tests for lazycomics.text_renderer.

Uses real Pillow images so we can verify output dimensions, pixel
content, and that the file is a valid PNG. Face detection is patched
at module level (``_detect_all_faces``) to keep tests deterministic
regardless of what MediaPipe finds in synthetic test images.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from PIL import Image

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics import text_renderer  # noqa: E402
from lazycomics.project import create_project  # noqa: E402
from lazycomics.text_renderer import render_text  # noqa: E402


# ---------------------------------------------------------------------------
# Test environment
# ---------------------------------------------------------------------------


class _Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cbml = self.root / "story.cbml"
        cbml.write_text(
            "## Test\naspect: 2:3\n\nPAGE preset:splash\n\nPANEL A\nloc: room\n> A test.\n"
        )
        self.project = create_project("p1", cbml, base_dir=self.root)

        # Default: no faces detected. Individual tests override.
        self._orig_detect = text_renderer._detect_all_faces
        text_renderer._detect_all_faces = lambda img: []

    def set_faces(self, faces: list[tuple[int, int, int, int]]):
        """Override face detection to return ``faces`` for this test."""
        text_renderer._detect_all_faces = lambda img: list(faces)

    def make_panel(self, panel_id: str, w: int = 800, h: int = 600,
                   colour=(200, 200, 200)) -> Path:
        path = self.project.panels_dir / f"{panel_id}.png"
        Image.new("RGB", (w, h), colour).save(path)
        return path

    def write_enriched(self, panel_id: str, **fields):
        data = {
            "panel_id": panel_id,
            "page_index": 0,
            "panel_index": 0,
            "aspect_ratio": 2 / 3,
            "caption_boxes": [],
            "sfx_lines": [],
            "dialogue_lines": [],
            "bubble_layout": [],
        }
        data.update(fields)
        (self.project.enriched_dir / f"{panel_id}.json").write_text(json.dumps(data))

    def close(self):
        text_renderer._detect_all_faces = self._orig_detect
        self.tmp.cleanup()


def _with_env(fn):
    def wrapper():
        env = _Env()
        try:
            fn(env)
        finally:
            env.close()
    wrapper.__name__ = fn.__name__
    return wrapper


# ---------------------------------------------------------------------------
# Basics: outputs, return value, dimensions
# ---------------------------------------------------------------------------


@_with_env
def test_writes_output_with_same_dimensions(env):
    env.make_panel("p1", w=800, h=600)
    env.write_enriched("p1", caption_boxes=[
        {"text": "Hi", "bg_color": "#000000", "text_color": "#ffffff",
         "position": "top-left"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png")
    assert out.size == (800, 600)


@_with_env
def test_returns_panel_id_to_path_dict(env):
    env.make_panel("p1")
    env.write_enriched("p1")
    result = render_text(env.project)
    assert "p1" in result
    assert result["p1"].name == "p1.png"


@_with_env
def test_multiple_panels(env):
    env.make_panel("page_1_panel_1")
    env.make_panel("page_1_panel_2")
    env.write_enriched("page_1_panel_1")
    env.write_enriched("page_1_panel_2")
    result = render_text(env.project)
    assert set(result) == {"page_1_panel_1", "page_1_panel_2"}


@_with_env
def test_panels_filter(env):
    env.make_panel("p1")
    env.make_panel("p2")
    env.write_enriched("p1")
    env.write_enriched("p2")
    result = render_text(env.project, panels=["p2"])
    assert set(result) == {"p2"}
    assert not (env.project.panels_text_dir / "p1.png").is_file()


@_with_env
def test_no_panels_returns_empty(env):
    assert render_text(env.project) == {}


# ---------------------------------------------------------------------------
# Skip / force
# ---------------------------------------------------------------------------


@_with_env
def test_skip_when_output_exists(env):
    env.make_panel("p1")
    env.write_enriched("p1")
    render_text(env.project)

    out = env.project.panels_text_dir / "p1.png"
    out.write_bytes(b"USER_OVERRIDE")
    render_text(env.project)  # default: force=False
    assert out.read_bytes() == b"USER_OVERRIDE"


@_with_env
def test_force_overwrites_existing(env):
    env.make_panel("p1")
    env.write_enriched("p1")
    render_text(env.project)

    out = env.project.panels_text_dir / "p1.png"
    out.write_bytes(b"USER_OVERRIDE")
    render_text(env.project, force=True)
    assert out.read_bytes() != b"USER_OVERRIDE"
    Image.open(out)  # parses cleanly


# ---------------------------------------------------------------------------
# Missing inputs
# ---------------------------------------------------------------------------


@_with_env
def test_missing_panel_image_skipped(env):
    env.write_enriched("p1")  # no panel image
    result = render_text(env.project, panels=["p1"])
    assert result == {}


@_with_env
def test_missing_enriched_json_skipped(env):
    env.make_panel("p1")  # no enriched JSON
    result = render_text(env.project, panels=["p1"])
    assert result == {}


@_with_env
def test_empty_overlays_no_crash(env):
    env.make_panel("p1")
    env.write_enriched("p1")  # no captions, no sfx, no dialogue
    render_text(env.project)
    assert (env.project.panels_text_dir / "p1.png").is_file()


# ---------------------------------------------------------------------------
# Caption positions (verify by sampling bg colour at the expected corner)
# ---------------------------------------------------------------------------


@_with_env
def test_caption_top_left_visible_at_top_left(env):
    env.make_panel("p1", w=800, h=600, colour=(200, 200, 200))
    env.write_enriched("p1", caption_boxes=[
        {"text": "T", "bg_color": "#ff0000", "text_color": "#ffffff",
         "position": "top-left"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")
    r, g, b = out.getpixel((30, 30))
    assert r > 200 and g < 80 and b < 80


@_with_env
def test_caption_bottom_right_visible_at_bottom_right(env):
    env.make_panel("p1", w=800, h=600, colour=(200, 200, 200))
    env.write_enriched("p1", caption_boxes=[
        {"text": "T", "bg_color": "#0000ff", "text_color": "#ffffff",
         "position": "bottom-right"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")
    w, h = out.size
    r, g, b = out.getpixel((w - 30, h - 30))
    assert b > 200 and r < 80 and g < 80


@_with_env
def test_caption_top_center_visible_at_top_center(env):
    env.make_panel("p1", w=800, h=600, colour=(200, 200, 200))
    env.write_enriched("p1", caption_boxes=[
        {"text": "T", "bg_color": "#00cc00", "text_color": "#ffffff",
         "position": "top-center"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")
    r, g, b = out.getpixel((400, 30))
    assert g > 150 and r < 80 and b < 80


# ---------------------------------------------------------------------------
# SFX
# ---------------------------------------------------------------------------


@_with_env
def test_sfx_renders_without_background_box(env):
    # Render SFX at top-left, then sample a non-text pixel near the top-left
    # margin. It should still be the panel's background colour (no SFX bg box).
    env.make_panel("p1", w=800, h=600, colour=(200, 200, 200))
    env.write_enriched("p1", sfx_lines=[
        {"text": "B", "color": "#ff0000", "position": "top-left"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")
    # Far enough from the actual glyph to be background, but inside margin range.
    r, g, b = out.getpixel((5, 5))
    assert abs(r - 200) < 30 and abs(g - 200) < 30 and abs(b - 200) < 30


@_with_env
def test_sfx_center_position_supported(env):
    env.make_panel("p1", w=800, h=600, colour=(200, 200, 200))
    env.write_enriched("p1", sfx_lines=[
        {"text": "BOOM!", "color": "#ff0000", "position": "center"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png")
    assert out.size == (800, 600)


# ---------------------------------------------------------------------------
# Dialogue bubbles
# ---------------------------------------------------------------------------


@_with_env
def test_dialogue_renders_bubble_at_top_by_default(env):
    """No faces detected -> bubbles default to the top of the speaker's region."""
    # _Env patches _detect_all_faces to return [] by default.
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))  # dark panel
    env.write_enriched("p1", dialogue_lines=[
        {"character": "NOVA", "text": "Hello world",
         "bubble_type": "speech"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")
    # Top band of the bubble should be predominantly white.
    white_count = sum(
        1 for x in range(35, 100)
        if all(c > 240 for c in out.getpixel((x, 30)))
    )
    assert white_count > 40, f"expected white bubble fill near the top, got {white_count} white px"


@_with_env
def test_dialogue_avoids_face_in_top_half_of_panel(env):
    """Face in the top half -> bubbles get pushed to the bottom of the region."""
    env.set_faces([(300, 60, 500, 260)])  # face in top half
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("p1", dialogue_lines=[
        {"character": "NOVA", "text": "Hello world", "bubble_type": "speech"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")

    # Top of panel should still be the dark background (no bubble there).
    top_dark = sum(
        1 for x in range(30, 100)
        if all(c < 80 for c in out.getpixel((x, 30)))
    )
    assert top_dark > 40, "expected dark panel background near top"

    # Near the bottom there should be a band of bubble-fill white pixels.
    bottom_white = sum(
        1 for x in range(35, 100)
        if all(c > 240 for c in out.getpixel((x, 545)))
    )
    assert bottom_white > 40, f"expected white bubble fill near bottom, got {bottom_white} white px"


@_with_env
def test_two_lines_same_speaker_stack_vertically(env):
    """Multiple lines from the *same* speaker stack within that speaker's region."""
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("p1",
        dialogue_lines=[
            {"character": "NOVA", "text": "A", "bubble_type": "speech"},
            {"character": "NOVA", "text": "B", "bubble_type": "speech"},
        ],
        bubble_layout=[
            {"character": "NOVA", "x_frac": 0, "y_frac": 0,
             "width_frac": 1, "height_frac": 1},
        ],
    )
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")

    # Both bubbles in NOVA's region (whole panel) — they stack vertically
    # at the left edge. Scan a column near x=50, count distinct white runs.
    runs = 0
    in_run = False
    for y in range(15, 250):
        r, g, b = out.getpixel((50, y))
        is_white = r >= 240 and g >= 240 and b >= 240
        if is_white and not in_run:
            runs += 1
            in_run = True
        elif not is_white:
            in_run = False
    assert runs == 2, f"expected 2 stacked bubbles, found {runs} white runs"

    # Lower half should still be panel background.
    r, g, b = out.getpixel((50, 400))
    assert r < 80 and g < 80 and b < 80


@_with_env
def test_two_speakers_go_to_distinct_regions(env):
    """Two speakers with left/right bubble_layout regions don't overlap."""
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("p1",
        dialogue_lines=[
            {"character": "NOVA", "text": "left line", "bubble_type": "speech"},
            {"character": "REX", "text": "right line", "bubble_type": "speech"},
        ],
        bubble_layout=[
            {"character": "NOVA", "x_frac": 0.0, "y_frac": 0,
             "width_frac": 0.5, "height_frac": 1},
            {"character": "REX", "x_frac": 0.5, "y_frac": 0,
             "width_frac": 0.5, "height_frac": 1},
        ],
    )
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")

    # Sample each bubble's left-padding strip (x=bubble_x+5, y in text band).
    # NOVA bubble at (20, 20); REX bubble at (420, 20).
    assert all(c > 240 for c in out.getpixel((25, 50))), (
        f"NOVA bubble missing at left half; got {out.getpixel((25, 50))}"
    )
    assert all(c > 240 for c in out.getpixel((425, 50))), (
        f"REX bubble missing at right half; got {out.getpixel((425, 50))}"
    )


@_with_env
def test_two_speakers_no_bubble_layout_auto_split(env):
    """Two speakers with no bubble_layout get auto-split — bubbles don't overlap."""
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("p1", dialogue_lines=[
        {"character": "NOVA", "text": "left line", "bubble_type": "speech"},
        {"character": "REX", "text": "right line", "bubble_type": "speech"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")
    # Fallback auto-split: NOVA gets left half, REX gets right half.
    # Sample left-padding of each bubble (white area, not on a glyph).
    assert all(c > 240 for c in out.getpixel((25, 50))), "NOVA bubble missing"
    assert all(c > 240 for c in out.getpixel((425, 50))), "REX bubble missing"


# ---------------------------------------------------------------------------
# Per-type bubble shapes
# ---------------------------------------------------------------------------


def _white_pixel_count(img, x0, y0, x1, y1) -> int:
    """Count fully-white pixels in the rect — proxy for bubble fill area."""
    count = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            r, g, b = img.getpixel((x, y))
            if r > 240 and g > 240 and b > 240:
                count += 1
    return count


@_with_env
def test_speech_bubble_fills_interior_left_padding(env):
    """A speech bubble's left-padding strip is filled white (no glyph there)."""
    env.make_panel("speech_p", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("speech_p", dialogue_lines=[
        {"character": "X", "text": "test text long enough to make a bubble",
         "bubble_type": "speech"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "speech_p.png").convert("RGB")
    # Bubble at (20, 20). Padding=12; text starts at x=32. At (25, 50) we're
    # in the left-padding strip (past the rounded corner radius), so the
    # rounded rectangle's fill should reach this pixel.
    r, g, b = out.getpixel((25, 50))
    assert r > 240 and g > 240 and b > 240, (
        f"speech bubble interior padding should be white; got ({r},{g},{b})"
    )


@_with_env
def test_shout_starburst_corner_is_outside_polygon(env):
    """Shout is a starburst — its bbox corners sit in the gaps between spikes."""
    env.make_panel("shout_p", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("shout_p", dialogue_lines=[
        {"character": "X", "text": "wide enough text to give a measurable bubble",
         "bubble_type": "shout"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "shout_p.png").convert("RGB")
    # The starburst's outer points reach only the cardinal directions on its
    # bounding ellipse. The bbox corner area, a few px diagonally in, is
    # OUTSIDE the polygon (between spikes) — should be panel background.
    # Equivalent check fails for speech (rounded rect fills near here).
    r, g, b = out.getpixel((23, 23))
    assert r < 80 and g < 80 and b < 80, (
        f"shout bbox corner should be panel bg (between spikes); got ({r},{g},{b})"
    )


@_with_env
def test_whisper_border_is_dashed(env):
    """Whisper's top edge alternates dark dashes with white gaps."""
    env.make_panel("whisper_p", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("whisper_p", dialogue_lines=[
        {"character": "X", "text": "wide enough text to span a few dashes",
         "bubble_type": "whisper"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "whisper_p.png").convert("RGB")

    # Bubble starts at margin=20 on the y-axis. Scan along that exact row inside
    # the bubble's x-range and count dark -> light transitions; many = dashed.
    transitions = 0
    last_dark = None
    for x in range(25, 500):
        r, g, b = out.getpixel((x, 20))
        is_dark = r < 80 and g < 80 and b < 80
        if last_dark is not None and is_dark != last_dark:
            transitions += 1
        last_dark = is_dark
    assert transitions >= 4, (
        f"whisper top edge should have dashes (>= 4 transitions); got {transitions}"
    )


@_with_env
def test_speech_and_shout_produce_different_silhouettes(env):
    """Same text rendered with different bubble types yields different fill areas."""
    text = "test text long enough to make a bubble"
    for pid, btype in (("speech_p", "speech"), ("shout_p", "shout")):
        env.make_panel(pid, w=800, h=600, colour=(40, 40, 40))
        env.write_enriched(pid, dialogue_lines=[
            {"character": "X", "text": text, "bubble_type": btype},
        ])
    render_text(env.project)
    speech_img = Image.open(env.project.panels_text_dir / "speech_p.png").convert("RGB")
    shout_img = Image.open(env.project.panels_text_dir / "shout_p.png").convert("RGB")
    # Sample the same top-left band of both panels.
    speech_white = _white_pixel_count(speech_img, 10, 10, 400, 120)
    shout_white = _white_pixel_count(shout_img, 10, 10, 400, 120)
    # They should differ by more than a trivial amount.
    assert abs(speech_white - shout_white) > 500, (
        f"speech vs shout fill areas should differ noticeably; "
        f"got speech={speech_white}, shout={shout_white}"
    )


# ---------------------------------------------------------------------------
# Combined: captions + SFX + dialogue in one panel — no crash, valid PNG
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tails (speaker-pointing)
# ---------------------------------------------------------------------------


@_with_env
def test_speech_tail_drawn_toward_detected_face_below_bubble(env):
    """Single speaker, single face in lower half -> tail points down to the face."""
    # Bubble defaults to the top of the panel; face at (370, 470) is below it.
    env.set_faces([(320, 420, 420, 520)])
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("p1",
        dialogue_lines=[
            {"character": "NOVA", "text": "Hello", "bubble_type": "speech"},
        ],
        bubble_layout=[
            {"character": "NOVA", "x_frac": 0, "y_frac": 0,
             "width_frac": 1, "height_frac": 1},
        ],
    )
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")

    # The tail fill is a white triangle from the bubble bottom-centre to the
    # face centre. We scan a wide x-corridor at several y depths between the
    # bubble and the face and look for white intrusions on the dark panel.
    found_white = False
    for y in (120, 180, 260, 340):
        for x in range(40, 380):
            r, g, b = out.getpixel((x, y))
            if r > 240 and g > 240 and b > 240:
                found_white = True
                break
        if found_white:
            break
    assert found_white, "tail fill (white) should extend from bubble toward face"


@_with_env
def test_thought_tail_renders_trailing_circles(env):
    """Thought bubble's tail is a sequence of small circles, not a triangle."""
    env.set_faces([(350, 450, 450, 550)])
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("p1",
        dialogue_lines=[
            {"character": "NOVA", "text": "Hmm", "bubble_type": "thought"},
        ],
        bubble_layout=[
            {"character": "NOVA", "x_frac": 0, "y_frac": 0,
             "width_frac": 1, "height_frac": 1},
        ],
    )
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")

    # Trailing circles are placed along the bubble->face line. Sample a wide
    # corridor between the bubble and the face and count rows that contain
    # any white intrusion — three small circles produce a handful of rows
    # each, well above any "stray noise" threshold.
    white_rows = 0
    for y in range(110, 460):
        for x in range(30, 400, 8):
            r, g, b = out.getpixel((x, y))
            if r > 240 and g > 240 and b > 240:
                white_rows += 1
                break
    assert white_rows >= 5, (
        f"thought tail should leave white pixels along the corridor "
        f"toward the face; got {white_rows} rows with whites"
    )


@_with_env
def test_shout_skips_tail(env):
    """Shout has no tail — the area below the bubble stays panel background."""
    env.set_faces([(350, 450, 450, 550)])
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("p1",
        dialogue_lines=[
            {"character": "NOVA", "text": "BANG!", "bubble_type": "shout"},
        ],
        bubble_layout=[
            {"character": "NOVA", "x_frac": 0, "y_frac": 0,
             "width_frac": 1, "height_frac": 1},
        ],
    )
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")

    # The midpoint between the bubble and the target face must remain panel
    # bg. With no tail being drawn, no white intrusion appears here.
    r, g, b = out.getpixel((50, 250))
    assert r < 80 and g < 80 and b < 80, (
        f"shout should not draw a tail; got white intrusion at ({r},{g},{b})"
    )


@_with_env
def test_no_tail_when_no_face_detected(env):
    """No detected face -> no tail (the bubble stands alone)."""
    # _Env default: faces = [].
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("p1",
        dialogue_lines=[
            {"character": "NOVA", "text": "Hello", "bubble_type": "speech"},
        ],
        bubble_layout=[
            {"character": "NOVA", "x_frac": 0, "y_frac": 0,
             "width_frac": 1, "height_frac": 1},
        ],
    )
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")
    # Midway down the panel near the left edge should still be panel bg.
    r, g, b = out.getpixel((50, 300))
    assert r < 80 and g < 80 and b < 80, (
        f"no face -> no tail; got white intrusion ({r},{g},{b})"
    )


@_with_env
def test_all_overlays_together(env):
    env.make_panel("p1", w=800, h=600, colour=(180, 180, 200))
    env.write_enriched("p1",
        caption_boxes=[
            {"text": "2:17 AM.", "bg_color": "#000000",
             "text_color": "#ffffff", "position": "top-left"},
        ],
        sfx_lines=[
            {"text": "CRASH!", "color": "#ff0000", "position": "bottom-right"},
        ],
        dialogue_lines=[
            {"character": "NOVA", "text": "Did you hear that?",
             "bubble_type": "speech"},
            {"character": "NOVA", "text": "...we should run.",
             "bubble_type": "whisper"},
        ],
    )
    render_text(env.project)
    out_path = env.project.panels_text_dir / "p1.png"
    assert out_path.is_file()
    out = Image.open(out_path)
    assert out.size == (800, 600)
