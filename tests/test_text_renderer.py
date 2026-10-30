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

from PIL import Image, ImageDraw

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
        # Face identity downloads a model and reads real faces; off by
        # default (tests that exercise it patch it themselves).
        self._orig_identity = text_renderer._identity_similarity
        text_renderer._identity_similarity = lambda img, faces, refs: None
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
        text_renderer._identity_similarity = self._orig_identity
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
# Geometry helpers for the placement tests. Track-2 placement is dynamic
# (bubbles go near their speaker / into negative space), so tests locate the
# rendered bubbles rather than assuming fixed corners.
# ---------------------------------------------------------------------------


def _white_bbox(img, region=None):
    """(x0, y0, x1, y1, count) of fully-white pixels, optionally within region."""
    W, H = img.size
    rx0, ry0, rx1, ry1 = region or (0, 0, W, H)
    xs = []
    ys = []
    n = 0
    for y in range(ry0, ry1):
        for x in range(rx0, rx1):
            r, g, b = img.getpixel((x, y))
            if r > 240 and g > 240 and b > 240:
                xs.append(x)
                ys.append(y)
                n += 1
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys), n)


def _white_y_bands(img, step=3, gap=6):
    """Contiguous vertical bands ``(y_top, y_bot)`` of rows that contain white.

    Two vertically-stacked bubbles separated by a dark gutter produce two
    bands; this is how we assert "stacked, non-overlapping" without caring
    about exact pixel coordinates.
    """
    W, H = img.size
    rows = [
        y for y in range(H)
        if any(all(c > 240 for c in img.getpixel((x, y))) for x in range(0, W, step))
    ]
    bands = []
    cur = None
    for y in rows:
        if cur and y <= cur[1] + gap:
            cur[1] = y
        else:
            if cur:
                bands.append(tuple(cur))
            cur = [y, y]
    if cur:
        bands.append(tuple(cur))
    return bands


def _rect_overlap(a, b):
    """Intersection area of two (x0, y0, x1, y1) rects."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy


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
def test_layout_sidecar_records_placements(env):
    # The renderer writes what it placed next to the PNG so review tooling
    # can check reading order / overlaps without re-deriving the layout.
    env.make_panel("p1", 800, 600)
    env.set_faces([(100, 300, 180, 380)])
    env.write_enriched(
        "p1",
        caption_boxes=[{"text": "Later.", "position": "top-left"}],
        dialogue_lines=[
            {"character": "NOVA", "text": "First line", "bubble_type": "speech"},
            {"character": "NOVA", "text": "", "bubble_type": "speech"},  # skipped
            {"character": "NOVA", "text": "Second line", "bubble_type": "shout"},
        ],
        bubble_layout=[{"character": "NOVA", "x_frac": 0.0, "y_frac": 0.0,
                        "width_frac": 1.0, "height_frac": 1.0}],
    )
    render_text(env.project)

    layout = json.loads(
        (env.project.panels_text_dir / "p1.layout.json").read_text(encoding="utf-8")
    )
    assert layout["size"] == [800, 600]
    assert layout["faces"] == [[100, 300, 180, 380]]
    assert len(layout["captions"]) == 1
    bubbles = layout["bubbles"]
    # Line indices are positions in dialogue_lines (CBML order); the empty
    # line is skipped, not renumbered.
    assert [b["line"] for b in bubbles] == [0, 2]
    assert bubbles[0]["tail"] is True       # speech + face found
    assert bubbles[1]["tail"] is False      # shouts never get a tail
    assert bubbles[0]["anchor"] == [140, 340]
    for b in bubbles:
        x0, y0, x1, y1 = b["rect"]
        assert 0 <= x0 < x1 <= 800 and 0 <= y0 < y1 <= 600


@_with_env
def test_layout_sidecar_written_for_textless_panel(env):
    env.make_panel("p1")
    env.write_enriched("p1")
    render_text(env.project)
    layout = json.loads((env.project.panels_text_dir / "p1.layout.json").read_text())
    assert layout == {"size": [800, 600]}


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
def test_dialogue_no_face_places_bubble_near_predicted_centre(env):
    """No faces: a lone speaker's bubble lands near the centre of its
    predicted (full-panel) region — not jammed into a corner."""
    # _Env patches _detect_all_faces to return [] by default.
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))  # dark panel
    env.write_enriched("p1", dialogue_lines=[
        {"character": "NOVA", "text": "Hello world",
         "bubble_type": "speech"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")
    bb = _white_bbox(out)
    assert bb is not None, "expected a rendered bubble"
    cx = (bb[0] + bb[2]) // 2
    assert 240 <= cx <= 560, f"bubble should sit near panel centre; cx={cx}"


@_with_env
def test_dialogue_avoids_face_in_top_half_of_panel(env):
    """A detected face is not covered: the bubble is placed clear of it."""
    face = (300, 60, 500, 260)  # face in the top half
    env.set_faces([face])
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("p1", dialogue_lines=[
        {"character": "NOVA", "text": "Hello world", "bubble_type": "speech"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")

    bb = _white_bbox(out)
    assert bb is not None, "expected a rendered bubble"
    # The face itself stays uncovered — its centre is still panel background.
    r, g, b = out.getpixel((400, 160))
    assert r < 80 and g < 80 and b < 80, (
        f"face centre should not be covered by a bubble; got ({r},{g},{b})"
    )
    # And the bubble's bounding box does not intrude on the face rect.
    assert _rect_overlap(bb[:4], face) == 0, (
        f"bubble bbox {bb[:4]} should not overlap face {face}"
    )


@_with_env
def test_two_lines_same_speaker_stack_vertically(env):
    """Consecutive lines from the same speaker stack into two vertically
    separated bubbles (the convention for one character's run of dialogue)."""
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

    bands = _white_y_bands(out)
    assert len(bands) == 2, f"expected 2 stacked bubbles, found bands={bands}"
    # The second bubble sits strictly below the first, with a gap between.
    assert bands[0][1] < bands[1][0], (
        f"bubbles should be vertically separated; bands={bands}"
    )


@_with_env
def test_two_speakers_go_to_distinct_regions(env):
    """Two speakers with left/right layout regions get bubbles on their own
    side of the panel, and the bubbles don't overlap."""
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

    left = _white_bbox(out, (0, 0, 400, 600))
    right = _white_bbox(out, (400, 0, 800, 600))
    assert left is not None, "NOVA bubble missing from left half"
    assert right is not None, "REX bubble missing from right half"
    assert (left[0] + left[2]) // 2 < 400 < (right[0] + right[2]) // 2, (
        "each speaker's bubble should sit on its own side"
    )
    assert _rect_overlap(left[:4], right[:4]) == 0, "bubbles should not overlap"


@_with_env
def test_two_speakers_no_bubble_layout_auto_split(env):
    """Two speakers with no bubble_layout get auto-split — each bubble lands
    on its own side and the two don't overlap."""
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("p1", dialogue_lines=[
        {"character": "NOVA", "text": "left line", "bubble_type": "speech"},
        {"character": "REX", "text": "right line", "bubble_type": "speech"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")

    left = _white_bbox(out, (0, 0, 400, 600))
    right = _white_bbox(out, (400, 0, 800, 600))
    assert left is not None, "NOVA bubble missing from left half"
    assert right is not None, "REX bubble missing from right half"
    assert (left[0] + left[2]) // 2 < 400 < (right[0] + right[2]) // 2
    assert _rect_overlap(left[:4], right[:4]) == 0, "bubbles should not overlap"


@_with_env
def test_speaker_bubbles_track_actual_face_positions(env):
    """Bubbles follow where faces actually are, not a uniform mid-height slot.

    A high-left face and a low-right face: the left speaker's bubble sits
    high and the right speaker's sits low, each clear of its own face. This
    is the core Track-2 behaviour — placement keyed on detected positions
    rather than the enricher's equal-slice prediction.
    """
    face_a = (120, 60, 220, 160)    # high, left
    face_b = (580, 420, 680, 520)   # low, right
    env.set_faces([face_a, face_b])
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("p1",
        dialogue_lines=[
            {"character": "NOVA", "text": "hi there", "bubble_type": "speech"},
            {"character": "REX", "text": "yo friend", "bubble_type": "speech"},
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

    left = _white_bbox(out, (0, 0, 400, 600))
    right = _white_bbox(out, (400, 0, 800, 600))
    assert left is not None and right is not None
    left_cy = (left[1] + left[3]) // 2
    right_cy = (right[1] + right[3]) // 2
    # Left bubble (high face) sits above the right bubble (low face).
    assert left_cy < right_cy, (
        f"bubbles should track face heights; left_cy={left_cy} right_cy={right_cy}"
    )
    # Neither bubble covers its associated face.
    assert _rect_overlap(left[:4], face_a) == 0, "left bubble covers the left face"
    assert _rect_overlap(right[:4], face_b) == 0, "right bubble covers the right face"


@_with_env
def test_bubble_prefers_negative_space_over_busy_region(env):
    """With no face to anchor to, the bubble drifts toward empty negative
    space and away from a visually busy (high-detail) region."""
    # Build a panel whose left half is busy (fine stripes -> strong edges)
    # and whose right half is flat. No pure white, so the white-bubble
    # detector isn't fooled by the background.
    img = Image.new("RGB", (800, 600), (110, 110, 110))
    px = img.load()
    for y in range(600):
        for x in range(0, 400):
            px[x, y] = (0, 0, 0) if (x // 3) % 2 == 0 else (130, 130, 130)
    img.save(env.project.panels_dir / "p1.png")
    env.write_enriched("p1", dialogue_lines=[
        {"character": "X", "text": "which side has room", "bubble_type": "speech"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")

    bb = _white_bbox(out, (0, 0, 800, 600))
    assert bb is not None, "expected a bubble"
    cx = (bb[0] + bb[2]) // 2
    # The flat right half is the negative space; the bubble should land there.
    assert cx > 400, f"bubble should avoid the busy left half; cx={cx}"


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
    bb = _white_bbox(out)
    assert bb is not None, "expected a speech bubble"
    # A few px inside the bubble's left edge, at mid-height, is left-padding:
    # the rounded rect's fill, not a glyph.
    midy = (bb[1] + bb[3]) // 2
    r, g, b = out.getpixel((bb[0] + 5, midy))
    assert r > 240 and g > 240 and b > 240, (
        f"speech bubble interior padding should be white; got ({r},{g},{b})"
    )


@_with_env
def test_shout_starburst_corner_is_outside_polygon(env):
    """Shout is a starburst — its centre is filled but its bbox corners sit
    in the gaps between spikes (panel background)."""
    env.make_panel("shout_p", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("shout_p", dialogue_lines=[
        {"character": "X", "text": "wide enough text to give a measurable bubble",
         "bubble_type": "shout"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "shout_p.png").convert("RGB")
    bb = _white_bbox(out)
    assert bb is not None, "expected a shout bubble"
    # A few px diagonally inside the bbox corner falls between two spikes, so
    # it stays panel background. (A rounded speech rect would be white here —
    # this is what distinguishes the starburst silhouette.)
    r, g, b = out.getpixel((bb[0] + 3, bb[1] + 3))
    assert r < 80 and g < 80 and b < 80, (
        f"shout bbox corner should be panel bg (between spikes); got ({r},{g},{b})"
    )


@_with_env
def test_whisper_border_is_dashed(env):
    """Whisper's top edge alternates dark dashes with light gaps."""
    env.make_panel("whisper_p", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("whisper_p", dialogue_lines=[
        {"character": "X", "text": "wide enough text to span a few dashes",
         "bubble_type": "whisper"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "whisper_p.png").convert("RGB")

    bb = _white_bbox(out)
    assert bb is not None, "expected a whisper bubble"
    # Scan just inside the top edge across the bubble width, counting
    # dark<->light transitions; a dashed border yields many.
    y = bb[1] + 1
    transitions = 0
    last_dark = None
    for x in range(bb[0] - 2, bb[2] + 2):
        r, g, b = out.getpixel((x, y))
        is_dark = r < 80 and g < 80 and b < 80
        if last_dark is not None and is_dark != last_dark:
            transitions += 1
        last_dark = is_dark
    assert transitions >= 4, (
        f"whisper top edge should have dashes (>= 4 transitions); got {transitions}"
    )


@_with_env
def test_speech_and_shout_produce_different_silhouettes(env):
    """Speech (solid rounded rect) and shout (spiky starburst) differ in shape.

    Compares the *fill ratio* — white pixels / bounding-box area — of each
    bubble. A rounded rectangle nearly fills its bbox; a starburst leaves
    large concave gaps between its spikes, so its fill ratio is much lower.
    This is robust to the absolute size of either shape.
    """
    text = "test text long enough to make a bubble"
    for pid, btype in (("speech_p", "speech"), ("shout_p", "shout")):
        env.make_panel(pid, w=800, h=600, colour=(40, 40, 40))
        env.write_enriched(pid, dialogue_lines=[
            {"character": "X", "text": text, "bubble_type": btype},
        ])
    render_text(env.project)
    speech_img = Image.open(env.project.panels_text_dir / "speech_p.png").convert("RGB")
    shout_img = Image.open(env.project.panels_text_dir / "shout_p.png").convert("RGB")

    def fill_ratio(img):
        xs, ys, n = [], [], 0
        for y in range(0, 600):
            for x in range(0, 800):
                r, g, b = img.getpixel((x, y))
                if r > 240 and g > 240 and b > 240:
                    xs.append(x); ys.append(y); n += 1
        if not xs:
            return 0.0
        area = (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1)
        return n / area

    speech_ratio = fill_ratio(speech_img)
    shout_ratio = fill_ratio(shout_img)
    assert speech_ratio - shout_ratio > 0.15, (
        f"speech should fill its bounding box far more densely than the "
        f"spiky shout starburst; got speech={speech_ratio:.3f}, "
        f"shout={shout_ratio:.3f}"
    )


# ---------------------------------------------------------------------------
# Combined: captions + SFX + dialogue in one panel — no crash, valid PNG
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tails (speaker-pointing)
# ---------------------------------------------------------------------------


@_with_env
def test_speech_tail_is_short_stub_toward_face(env):
    """Speech tail is a short stub just below the bubble — not a leader line.

    The face sits low in the panel (y~470). The tail should point toward it
    but remain a short stub near the bubble; it must NOT stretch down the
    panel to physically reach the face.
    """
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

    whites = [(x, y) for y in range(0, 600) for x in range(0, 800)
              if all(c > 240 for c in out.getpixel((x, y)))]
    assert whites, "expected a white speech bubble"
    xs = [x for x, _ in whites]
    ys = [y for _, y in whites]
    cx = (min(xs) + max(xs)) // 2

    def col_bottom(x):
        col = [y for y in range(0, 600) if all(c > 240 for c in out.getpixel((x, y)))]
        return max(col) if col else 0

    center_bot = col_bottom(cx)            # body + tail at the centre
    edge_bot = col_bottom(min(xs) + 3)     # body only, away from the tail
    max_tail = max(16, int(min(800, 600) * 0.05))  # mirrors the renderer

    # The tail protrudes below the bubble body at the centre column only.
    assert center_bot > edge_bot + 8, (
        f"expected a tail protruding below the body at centre; "
        f"center_bot={center_bot}, edge_bot={edge_bot}"
    )
    # ...but it is a SHORT stub, not a panel-spanning leader line.
    assert center_bot - edge_bot <= max_tail + 12, (
        f"tail should be a short stub (<= {max_tail}px); "
        f"protrusion={center_bot - edge_bot}"
    )
    # The bubble is placed near the speaker (here, just above the face), not
    # parked elsewhere on the panel.
    bubble_cy = (min(ys) + max(ys)) // 2
    assert abs(bubble_cy - 470) < 170, (
        f"bubble should sit near the speaker's face (y~470); bubble_cy={bubble_cy}"
    )
    # The face itself stays uncovered.
    r, g, b = out.getpixel((370, 470))
    assert r < 80 and g < 80 and b < 80, (
        f"face centre should not be covered; got ({r},{g},{b})"
    )


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


# ---------------------------------------------------------------------------
# W3: line spacing, area-based sizing, attribution, reading order, persistence
# ---------------------------------------------------------------------------


def _layout(env, panel_id="p1"):
    return json.loads(
        (env.project.panels_text_dir / f"{panel_id}.layout.json").read_text(encoding="utf-8")
    )


def _dark_row_bands(img, rect, dark=100):
    """Contiguous row bands inside ``rect`` that contain dark (text) pixels.

    Scans clear of the outline and of the rounded corners (radius <= 20),
    where the dark panel background shows through.
    """
    x0, y0, x1, y1 = rect
    rows = [any(max(img.getpixel((x, y))) < dark for x in range(x0 + 22, x1 - 22))
            for y in range(y0 + 3, y1 - 3)]
    bands, inside = 0, False
    for has_ink in rows:
        if has_ink and not inside:
            bands += 1
        inside = has_ink
    return bands


@_with_env
def test_wrapped_lines_do_not_collide(env):
    # Golden page_4_panel_1: "are more" (no ascenders/descenders) followed by
    # "of you?" drew on top of each other when lines advanced by their own
    # ink height. Advancing by the font's line height keeps two clear rows.
    orig = text_renderer._wrap_text
    text_renderer._wrap_text = lambda text, font, width, draw: ["are more", "of you?"]
    try:
        env.make_panel("p1", 800, 600, colour=(40, 40, 40))
        env.write_enriched("p1", dialogue_lines=[
            {"character": "NOVA", "text": "are more of you?", "bubble_type": "speech"},
        ])
        render_text(env.project)
    finally:
        text_renderer._wrap_text = orig
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")
    rect = _layout(env)["bubbles"][0]["rect"]
    assert _dark_row_bands(out, rect) == 2


def test_font_size_from_panel_area_keeps_4x3_size():
    cfg = {"min_font": 16, "font_scale": 1.0}
    # A 4:3 panel keeps the size the old width-based ratio gave it (+-1px)...
    assert abs(text_renderer._font_px(0.035, 1024, 768, cfg) - int(1024 * 0.030)) <= 1
    # ...while a tall narrow strip is no longer sized by its width alone.
    assert text_renderer._font_px(0.035, 448, 1024, cfg) > int(448 * 0.030)


def test_narrow_panel_bubbles_use_most_of_the_width():
    frac = text_renderer._BUBBLE_MAX_WIDTH_FRAC
    # 224px strip: wrap to the panel minus margins, not 42% of its width.
    assert text_renderer._wrap_width(frac, 224, 1024, 20) == 184
    # Wide panels are unaffected by the cap.
    assert text_renderer._wrap_width(frac, 1024, 768, 20) == int((1024 * 768) ** 0.5 * frac)


def _regions(chars, W, H=1000):
    """Equal left-to-right slices, as the enricher predicts them."""
    n = len(chars)
    return {c: (i * W // n, 0, W // n, H) for i, c in enumerate(chars)}


def _face(cx, h, W=1000, y=300):
    x = int(cx * W)
    return (x - h // 2, y, x + h // 2, y + h)


def test_match_ignores_background_faces_by_size_and_distance():
    # Golden page_1_panel_1: six faces for two characters. Rank-matching the
    # speakers gave Gerald a background face and Yuki Gerald's face.
    faces = [_face(0.07, 40), _face(0.26, 74), _face(0.30, 60),
             _face(0.50, 29), _face(0.67, 72), _face(0.92, 58)]
    got = text_renderer._match_characters_to_faces(
        ["GERALD", "YUKI"], _regions(["GERALD", "YUKI"], 1000), faces, 1000)
    assert got == {"GERALD": _face(0.26, 74), "YUKI": _face(0.67, 72)}


def test_match_counts_silent_characters():
    # Golden page_2_panel_2: Ranjeet and Yuki speak, Gerald (left) is silent.
    # Matching only the speakers handed them the two leftmost faces.
    chars = ["GERALD", "YUKI", "RANJEET"]
    faces = [_face(0.08, 49), _face(0.24, 137), _face(0.60, 103), _face(0.80, 121)]
    got = text_renderer._match_characters_to_faces(chars, _regions(chars, 1000), faces, 1000)
    assert got == {"GERALD": _face(0.24, 137), "YUKI": _face(0.60, 103),
                   "RANJEET": _face(0.80, 121)}


def test_match_fails_closed_when_ambiguous():
    # One face exactly between two characters' predicted centres: neither
    # gets it (no tail beats a wrong tail).
    chars = ["A", "B"]
    got = text_renderer._match_characters_to_faces(chars, _regions(chars, 1000),
                                                    [_face(0.50, 80)], 1000)
    assert got == {}


def test_match_fewer_faces_leaves_unseen_speaker_unmatched():
    # Golden page_4_panel_1: four characters, two faces. Gerald isn't in the
    # panel; he must not inherit someone else's face.
    chars = ["GERALD", "YUKI", "RANJEET", "DAVID"]
    got = text_renderer._match_characters_to_faces(
        chars, _regions(chars, 1000), [_face(0.31, 62), _face(0.82, 54)], 1000)
    assert "GERALD" not in got and len(got) == 2


@_with_env
def test_alternating_exchange_reads_in_cbml_order(env):
    # The June page_3_panel_1 failure: two speakers at mid-height trading
    # four lines came out fully reversed (each later line placed higher).
    from lazycomics import review
    env.make_panel("p1", 912, 1024, colour=(40, 40, 40))
    env.set_faces([(200, 420, 280, 500), (600, 410, 680, 490)])
    env.write_enriched("p1",
        dialogue_lines=[
            {"character": "YUKI", "text": "How are you so calm?", "bubble_type": "speech"},
            {"character": "GERALD", "text": "I have seen worse crashes.", "bubble_type": "speech"},
            {"character": "YUKI", "text": "Like 2008?", "bubble_type": "speech"},
            {"character": "GERALD", "text": "Much earlier.", "bubble_type": "speech"},
        ],
        bubble_layout=[
            {"character": "GERALD", "x_frac": 0.0, "y_frac": 0,
             "width_frac": 0.5, "height_frac": 1},
            {"character": "YUKI", "x_frac": 0.5, "y_frac": 0,
             "width_frac": 0.5, "height_frac": 1},
        ],
    )
    render_text(env.project)
    layout = _layout(env)
    assert review._reading_order_issues(layout["bubbles"]) == []
    assert review._overlap_issues(layout) == []
    # Each line's tail goes to its own speaker's face.
    for b in layout["bubbles"]:
        assert b["tail"]
        if b["speaker"] == "GERALD":
            assert b["anchor"][0] < 456
        else:
            assert b["anchor"][0] > 456


def test_layout_moves_bubbles_off_captions_and_sfx():
    # Lay a bubble out, put an obstacle (a caption) exactly where it landed,
    # lay it out again: it must move clear.
    face = (360, 300, 440, 380)
    bubble = {"w": 170, "h": 50, "speaker": "NOVA", "anchor": (400, 340),
              "has_face": True, "face": face, "tailed": True}
    busy = text_renderer._busyness_map(Image.new("RGB", (800, 600), (40, 40, 40)))
    (x, y), = text_renderer._layout_bubbles([bubble], [face], [], busy, 800, 600, 20,
                                            clearance=30)
    caption = [x - 10, y - 10, x + 180, y + 60]
    (x2, y2), = text_renderer._layout_bubbles([bubble], [face], [caption], busy, 800, 600, 20,
                                              clearance=30)
    assert _rect_overlap((x2, y2, x2 + 170, y2 + 50), caption) == 0


@_with_env
def test_rendered_bubble_clears_caption(env):
    env.make_panel("p1", 800, 600, colour=(40, 40, 40))
    env.set_faces([(360, 300, 440, 380)])
    env.write_enriched("p1",
        caption_boxes=[{"text": "Meanwhile, at the office.", "position": "top-center"}],
        dialogue_lines=[{"character": "NOVA", "text": "Hello there", "bubble_type": "speech"}],
    )
    render_text(env.project)
    layout = _layout(env)
    assert _rect_overlap(layout["bubbles"][0]["rect"], layout["captions"][0]) == 0


@_with_env
def test_decisions_persisted_to_enriched_json(env):
    import hashlib
    env.make_panel("p1", 800, 600, colour=(40, 40, 40))
    env.set_faces([(300, 300, 380, 380)])
    env.write_enriched("p1", dialogue_lines=[
        {"character": "NOVA", "text": "Hello", "bubble_type": "speech"}])
    render_text(env.project)
    data = json.loads((env.project.enriched_dir / "p1.json").read_text(encoding="utf-8"))
    assert data["detected_faces"] == [[300, 300, 380, 380]]
    assert data["actual_character_regions"] == {"NOVA": [300, 300, 380, 380]}
    panel_bytes = (env.project.panels_dir / "p1.png").read_bytes()
    assert data["detected_faces_for"] == hashlib.sha1(panel_bytes).hexdigest()


@_with_env
def test_rerender_reuses_persisted_faces_for_same_image(env):
    calls = []
    env.make_panel("p1", 800, 600, colour=(40, 40, 40))
    env.write_enriched("p1", dialogue_lines=[
        {"character": "NOVA", "text": "Hello", "bubble_type": "speech"}])
    text_renderer._detect_all_faces = lambda img: calls.append(1) or [(300, 300, 380, 380)]
    render_text(env.project)
    render_text(env.project, force=True)
    assert len(calls) == 1
    # A regenerated panel image has a different hash: detect afresh.
    env.make_panel("p1", 800, 600, colour=(60, 60, 60))
    render_text(env.project, force=True)
    assert len(calls) == 2


@_with_env
def test_hand_edited_character_region_is_honoured(env):
    env.make_panel("p1", 800, 600, colour=(40, 40, 40))
    env.set_faces([(100, 300, 180, 380), (600, 300, 680, 380)])
    env.write_enriched("p1",
        dialogue_lines=[{"character": "NOVA", "text": "Hello", "bubble_type": "speech"}],
        bubble_layout=[{"character": "NOVA", "x_frac": 0.0, "y_frac": 0,
                        "width_frac": 1.0, "height_frac": 1}])
    render_text(env.project)
    # A user corrects the match by hand: NOVA is the right-hand face.
    path = env.project.enriched_dir / "p1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["actual_character_regions"] = {"NOVA": [600, 300, 680, 380]}
    path.write_text(json.dumps(data), encoding="utf-8")
    render_text(env.project, force=True)
    assert _layout(env)["bubbles"][0]["anchor"] == [640, 340]


@_with_env
def test_hand_added_face_counts_as_a_face(env):
    # Problem 14: the detector missed the speaker's face; the user adds it
    # as their region. It must be avoided like a face, and the tail aimed at
    # it must count as aimed at a face.
    from lazycomics import review
    env.make_panel("p1", 800, 600, colour=(40, 40, 40))
    env.set_faces([])
    env.write_enriched("p1", dialogue_lines=[
        {"character": "NOVA", "text": "Hello", "bubble_type": "speech"}])
    render_text(env.project)
    path = env.project.enriched_dir / "p1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["actual_character_regions"] = {"NOVA": [360, 300, 440, 380]}
    path.write_text(json.dumps(data), encoding="utf-8")
    render_text(env.project, force=True)
    layout = _layout(env)
    assert [360, 300, 440, 380] in layout["faces"]
    assert layout["bubbles"][0]["tail"]
    assert review._tail_issues(layout) == []
    assert _rect_overlap(layout["bubbles"][0]["rect"], (360, 300, 440, 380)) == 0


def test_tail_is_lengthened_to_a_visible_stub():
    img = Image.new("RGB", (400, 400), (40, 40, 40))
    draw = ImageDraw.Draw(img)
    rect = (100, 100, 300, 160)
    # Speaker only 3px below the bubble: the tail still reaches 12px.
    assert text_renderer._draw_tail(draw, rect, (200, 163), "speech", 40)
    r, g, b = img.getpixel((200, 160 + text_renderer._TAIL_STUB_PX - 4))
    assert r > 200 and g > 200 and b > 200


def test_tail_reports_not_drawn_when_speaker_is_level_with_bubble():
    draw = ImageDraw.Draw(Image.new("RGB", (400, 400)))
    assert text_renderer._draw_tail(draw, (100, 100, 300, 160), (350, 130), "speech", 40) is False


# Identity blend — similarities are the real SFace values measured on the
# golden panels in the W3.2 spike.


def test_identity_fixes_mirrored_composition():
    # Golden page_4_panel_1: the generator drew David left and Ranjeet right;
    # the CBML order predicts David far right. Position alone gives the left
    # face to Yuki and the right one to David.
    chars = ["GERALD", "YUKI", "RANJEET", "DAVID"]
    left, right = _face(0.31, 62), _face(0.82, 54)
    sims = {"DAVID": [0.34, 0.19], "RANJEET": [0.28, 0.26],
            "YUKI": [0.12, 0.15], "GERALD": [-0.02, -0.00]}
    got = text_renderer._match_characters_to_faces(chars, _regions(chars, 1000),
                                                    [left, right], 1000, similarity=sims)
    assert got == {"DAVID": left, "RANJEET": right}


def test_weak_identity_does_not_overrule_correct_position():
    # Golden page_2_panel_2: identity alone swaps Gerald (drawn off-model)
    # and Ranjeet; blended with position, the correct match stands.
    chars = ["GERALD", "YUKI", "RANJEET"]
    bg, g, y, r = _face(0.08, 49), _face(0.24, 137), _face(0.60, 103), _face(0.80, 121)
    sims = {"GERALD": [None, 0.21, 0.16, 0.28], "YUKI": [None, 0.29, 0.54, 0.38],
            "RANJEET": [None, 0.31, 0.37, 0.24]}
    got = text_renderer._match_characters_to_faces(chars, _regions(chars, 1000),
                                                    [bg, g, y, r], 1000, similarity=sims)
    assert got == {"GERALD": g, "YUKI": y, "RANJEET": r}


def test_identity_without_usable_evidence_falls_back_to_position():
    # No face could be aligned: behave exactly like position-only matching,
    # including failing closed on an ambiguous face.
    chars = ["A", "B"]
    face = [_face(0.50, 80)]
    sims = {"A": [None], "B": [None]}
    assert text_renderer._match_characters_to_faces(
        chars, _regions(chars, 1000), face, 1000, similarity=sims) == {}


@_with_env
def test_identity_used_only_with_references_and_several_characters(env):
    calls = []
    text_renderer._identity_similarity = (
        lambda img, faces, refs: calls.append(dict(refs)) or {c: [0.0] * len(faces) for c in refs})
    env.make_panel("p1", 800, 600, colour=(40, 40, 40))
    env.set_faces([(100, 300, 180, 380), (600, 300, 680, 380)])
    two = [{"identifier": "NOVA", "reference_images": ["/refs/nova.png"]},
           {"identifier": "REX", "reference_images": []}]
    # As the enricher writes it: bubble_layout names every panel character.
    layout_two = [
        {"character": "NOVA", "x_frac": 0.0, "y_frac": 0, "width_frac": 0.5, "height_frac": 1},
        {"character": "REX", "x_frac": 0.5, "y_frac": 0, "width_frac": 0.5, "height_frac": 1},
    ]
    env.write_enriched("p1", characters=two, bubble_layout=layout_two, dialogue_lines=[
        {"character": "NOVA", "text": "Hi", "bubble_type": "speech"}])
    render_text(env.project)
    assert calls == [{"NOVA": "/refs/nova.png"}]
    assert _layout(env)["matching"] == "position+identity"

    calls.clear()
    env.write_enriched("p1", characters=two[:1], bubble_layout=layout_two[:1], dialogue_lines=[
        {"character": "NOVA", "text": "Hi", "bubble_type": "speech"}])
    render_text(env.project, force=True)
    assert calls == [] and _layout(env)["matching"] == "position"
