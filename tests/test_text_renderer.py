"""Tests for lazycomics.text_renderer.

Uses real Pillow images so we can verify output dimensions, pixel
content, and that the file is a valid PNG. The face-detection function
is patched at module level to keep tests deterministic regardless of
which faces MediaPipe finds in synthetic test images.
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

        # Force bubble placement to top by default so pixel sampling is predictable.
        self._orig_safe_top = text_renderer._bubbles_should_go_top
        text_renderer._bubbles_should_go_top = lambda img: True

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
        }
        data.update(fields)
        (self.project.enriched_dir / f"{panel_id}.json").write_text(json.dumps(data))

    def close(self):
        text_renderer._bubbles_should_go_top = self._orig_safe_top
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
def test_dialogue_renders_bubble_at_top_when_safe_top(env):
    # _bubbles_should_go_top is patched to True in _Env (default).
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))  # dark panel
    env.write_enriched("p1", dialogue_lines=[
        {"character": "NOVA", "text": "Hello world",
         "bubble_type": "speech"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")
    # Top band of the bubble should be predominantly white (above the text glyphs).
    white_count = sum(
        1 for x in range(35, 100)
        if all(c > 240 for c in out.getpixel((x, 30)))
    )
    assert white_count > 40, f"expected white bubble fill near the top, got {white_count} white px"


@_with_env
def test_dialogue_renders_bubble_at_bottom_when_faces_in_top(env):
    text_renderer._bubbles_should_go_top = lambda img: False
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("p1", dialogue_lines=[
        {"character": "NOVA", "text": "Hello world", "bubble_type": "speech"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")

    # Top of panel should still be the dark background.
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
def test_multiple_dialogue_lines_stack_vertically(env):
    env.make_panel("p1", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("p1", dialogue_lines=[
        {"character": "NOVA", "text": "A", "bubble_type": "speech"},
        {"character": "REX", "text": "B", "bubble_type": "speech"},
    ])
    render_text(env.project)
    out = Image.open(env.project.panels_text_dir / "p1.png").convert("RGB")

    # Scan a column inside the bubble x-range and count distinct "white" runs:
    # two separately-stacked bubbles produce two runs separated by a dark gap.
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

    # And the lower half should remain panel background.
    r, g, b = out.getpixel((50, 400))
    assert r < 80 and g < 80 and b < 80


@_with_env
def test_bubble_type_shout_uses_thicker_border(env):
    # Render two panels side by side and compare a pixel just inside the border.
    # Shout uses width=4, speech uses width=2 — at offset 3 from edge,
    # shout should still be border (black) while speech is fill (white).
    env.make_panel("speech_p", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("speech_p", dialogue_lines=[
        {"character": "X", "text": "test text long enough to make a bubble",
         "bubble_type": "speech"},
    ])
    env.make_panel("shout_p", w=800, h=600, colour=(40, 40, 40))
    env.write_enriched("shout_p", dialogue_lines=[
        {"character": "X", "text": "test text long enough to make a bubble",
         "bubble_type": "shout"},
    ])
    render_text(env.project)

    speech_out = Image.open(env.project.panels_text_dir / "speech_p.png").convert("RGB")
    shout_out = Image.open(env.project.panels_text_dir / "shout_p.png").convert("RGB")

    # Bubbles start at x=20 (margin). Sample inside-edge: at x = margin+3 = 23.
    # Sample in the bubble's vertical interior so we're definitely on a side edge.
    speech_px = speech_out.getpixel((23, 50))
    shout_px = shout_out.getpixel((23, 50))
    # Shout border is thicker → pixel deeper inside is still border (dark).
    speech_brightness = sum(speech_px)
    shout_brightness = sum(shout_px)
    assert shout_brightness < speech_brightness


# ---------------------------------------------------------------------------
# Combined: captions + SFX + dialogue in one panel — no crash, valid PNG
# ---------------------------------------------------------------------------


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
