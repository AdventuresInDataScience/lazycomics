"""Tests for lazycomics.assembler.

Builds mock Comic objects (SimpleNamespace) so we don't depend on
cbml_parser being installed. Writes real PIL panel images so we can
verify output dimensions and sample pixels for layout checks.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics import assembler  # noqa: E402
from lazycomics.assembler import (  # noqa: E402
    _assemble_from_comic,
    _page_filename,
    assemble_pages,
)
from lazycomics.project import create_project  # noqa: E402


# ---------------------------------------------------------------------------
# Mock-comic builders (mirror the parser API surface we actually use)
# ---------------------------------------------------------------------------


def _slot(c1, c2, r1, r2):
    return SimpleNamespace(cols=(c1, c2), rows=(r1, r2))


def _panel(label, slot):
    return SimpleNamespace(label=label, slot=slot)


def _page(index, panels, span=1):
    return SimpleNamespace(index=index, panels=list(panels), span=span)


def _comic(pages, aspect=(2, 3)):
    return SimpleNamespace(pages=list(pages), aspect=aspect)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


class _Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        # Patch cwd for config tests
        self._orig_cwd = Path.cwd
        Path.cwd = staticmethod(lambda: self.root)

        cbml = self.root / "story.cbml"
        cbml.write_text(
            "## Test\naspect: 2:3\n\nPAGE preset:splash\n\nPANEL A\nloc: room\n> A test.\n"
        )
        self.project = create_project("p1", cbml, base_dir=self.root)

    def make_panel(self, panel_id, w, h, colour):
        path = self.project.panels_text_dir / f"{panel_id}.png"
        Image.new("RGB", (w, h), colour).save(path)
        return path

    def write_config(self, body):
        (self.root / "lazycomics_config.yaml").write_text(body)

    def close(self):
        Path.cwd = self._orig_cwd
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
# Basics
# ---------------------------------------------------------------------------


@_with_env
def test_single_page_writes_one_png(env):
    env.make_panel("page_1_panel_1", 600, 900, (255, 0, 0))
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1))])])
    _assemble_from_comic(env.project, comic, page_height_px=900)
    assert (env.project.pages_dir / "page_001.png").is_file()


@_with_env
def test_returns_dict_of_physical_page_to_path(env):
    env.make_panel("page_1_panel_1", 600, 900, (100, 100, 100))
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1))])])
    result = _assemble_from_comic(env.project, comic, page_height_px=900)
    assert result == {1: env.project.pages_dir / "page_001.png"}


@_with_env
def test_page_dimensions_match_aspect_and_height(env):
    env.make_panel("page_1_panel_1", 600, 900, (100, 100, 100))
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1))])], aspect=(2, 3))
    _assemble_from_comic(env.project, comic, page_height_px=900)
    out = Image.open(env.project.pages_dir / "page_001.png")
    assert out.size == (600, 900)  # 2:3 at h=900 → w=600


@_with_env
def test_square_aspect_produces_square_page(env):
    env.make_panel("page_1_panel_1", 500, 500, (100, 100, 100))
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1))])], aspect=(1, 1))
    _assemble_from_comic(env.project, comic, page_height_px=900)
    out = Image.open(env.project.pages_dir / "page_001.png")
    assert out.size == (900, 900)


@_with_env
def test_manga_aspect_5_to_7(env):
    env.make_panel("page_1_panel_1", 500, 700, (100, 100, 100))
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1))])], aspect=(5, 7))
    _assemble_from_comic(env.project, comic, page_height_px=700)
    out = Image.open(env.project.pages_dir / "page_001.png")
    assert out.size == (500, 700)


@_with_env
def test_filename_is_zero_padded_three_digit(env):
    env.make_panel("page_1_panel_1", 600, 900, (100, 100, 100))
    pages = [_page(i, [_panel("A", _slot(1, 1, 1, 1))]) for i in range(3)]
    for i in range(3):
        env.make_panel(f"page_{i+1}_panel_1", 600, 900, (i * 80, i * 80, i * 80))
    comic = _comic(pages)
    result = _assemble_from_comic(env.project, comic, page_height_px=900)
    assert set(result) == {1, 2, 3}
    for p in [1, 2, 3]:
        assert (env.project.pages_dir / f"page_{p:03d}.png").is_file()


# ---------------------------------------------------------------------------
# Grid layouts — pixel sampling
# ---------------------------------------------------------------------------


@_with_env
def test_2x2_grid_places_panels_in_correct_quadrants(env):
    # Distinct colours for each panel — sample centre of each quadrant.
    env.make_panel("page_1_panel_1", 500, 500, (255, 0, 0))    # red — top-left
    env.make_panel("page_1_panel_2", 500, 500, (0, 255, 0))    # green — top-right
    env.make_panel("page_1_panel_3", 500, 500, (0, 0, 255))    # blue — bottom-left
    env.make_panel("page_1_panel_4", 500, 500, (255, 255, 0))  # yellow — bottom-right

    comic = _comic([_page(0, [
        _panel("A", _slot(1, 1, 1, 1)),
        _panel("B", _slot(2, 2, 1, 1)),
        _panel("C", _slot(1, 1, 2, 2)),
        _panel("D", _slot(2, 2, 2, 2)),
    ])], aspect=(2, 3))
    _assemble_from_comic(env.project, comic, page_height_px=900, gutter_px=10)

    out = Image.open(env.project.pages_dir / "page_001.png").convert("RGB")
    W, H = out.size

    # Sample well inside each quadrant
    tl = out.getpixel((W // 4, H // 4))
    tr = out.getpixel((3 * W // 4, H // 4))
    bl = out.getpixel((W // 4, 3 * H // 4))
    br = out.getpixel((3 * W // 4, 3 * H // 4))

    assert tl[0] > 200 and tl[1] < 60, f"TL should be red, got {tl}"
    assert tr[1] > 200 and tr[0] < 60, f"TR should be green, got {tr}"
    assert bl[2] > 200 and bl[0] < 60, f"BL should be blue, got {bl}"
    assert br[0] > 200 and br[1] > 200, f"BR should be yellow, got {br}"


@_with_env
def test_wide_panel_spans_multiple_columns(env):
    # 3x1 grid; panel A spans cols 1-2 (wide), panel B is col 3.
    env.make_panel("page_1_panel_1", 800, 400, (255, 0, 0))   # wide red
    env.make_panel("page_1_panel_2", 400, 400, (0, 0, 255))   # narrow blue

    comic = _comic([_page(0, [
        _panel("A", _slot(1, 2, 1, 1)),
        _panel("B", _slot(3, 3, 1, 1)),
    ])], aspect=(3, 1))
    _assemble_from_comic(env.project, comic, page_height_px=300, gutter_px=0)

    out = Image.open(env.project.pages_dir / "page_001.png").convert("RGB")
    W, H = out.size  # 900 × 300

    # 2/3 of the width is panel A (red); right third is panel B (blue)
    r, g, b = out.getpixel((W // 3, H // 2))
    assert r > 200 and b < 60, f"col 1-2 should be red, got ({r},{g},{b})"
    r, g, b = out.getpixel((5 * W // 6, H // 2))
    assert b > 200 and r < 60, f"col 3 should be blue, got ({r},{g},{b})"


@_with_env
def test_gutter_fills_with_bg_color(env):
    env.make_panel("page_1_panel_1", 500, 500, (255, 0, 0))
    env.make_panel("page_1_panel_2", 500, 500, (255, 0, 0))

    comic = _comic([_page(0, [
        _panel("A", _slot(1, 1, 1, 1)),
        _panel("B", _slot(2, 2, 1, 1)),
    ])], aspect=(2, 1))
    _assemble_from_comic(
        env.project, comic,
        page_height_px=300, gutter_px=20, bg_color="#00ff00",
    )

    out = Image.open(env.project.pages_dir / "page_001.png").convert("RGB")
    W, H = out.size  # 600 × 300
    # Centre column is the gutter (20px wide centred at x=300)
    r, g, b = out.getpixel((W // 2, H // 2))
    assert g > 200 and r < 60 and b < 60, f"gutter should be green, got ({r},{g},{b})"


# ---------------------------------------------------------------------------
# Spreads
# ---------------------------------------------------------------------------


@_with_env
def test_spread_2_full_bleed_writes_two_physical_pages(env):
    env.make_panel("page_1_panel_1", 1200, 900, (100, 100, 100))
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1))], span=2)], aspect=(2, 3))
    _assemble_from_comic(env.project, comic, page_height_px=900)

    assert (env.project.pages_dir / "page_001.png").is_file()
    assert (env.project.pages_dir / "page_002.png").is_file()
    p1 = Image.open(env.project.pages_dir / "page_001.png")
    p2 = Image.open(env.project.pages_dir / "page_002.png")
    # Each half is one physical page: 600 × 900.
    assert p1.size == (600, 900)
    assert p2.size == (600, 900)


@_with_env
def test_spread_2_with_2x1_grid_each_panel_on_own_page(env):
    env.make_panel("page_1_panel_1", 600, 900, (255, 0, 0))
    env.make_panel("page_1_panel_2", 600, 900, (0, 0, 255))
    comic = _comic([_page(0, [
        _panel("A", _slot(1, 1, 1, 1)),
        _panel("B", _slot(2, 2, 1, 1)),
    ], span=2)], aspect=(2, 3))
    _assemble_from_comic(env.project, comic, page_height_px=900, gutter_px=0)

    p1 = Image.open(env.project.pages_dir / "page_001.png").convert("RGB")
    p2 = Image.open(env.project.pages_dir / "page_002.png").convert("RGB")
    # Left page is red; right page is blue.
    r, g, b = p1.getpixel((300, 450))
    assert r > 200 and b < 60
    r, g, b = p2.getpixel((300, 450))
    assert b > 200 and r < 60


@_with_env
def test_spread_2_with_4x1_grid_narrow_strips(env):
    # 4 narrow panels split across 2 physical pages — 2 panels per page.
    colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    for i, c in enumerate(colours):
        env.make_panel(f"page_1_panel_{i+1}", 300, 900, c)
    comic = _comic([_page(0, [
        _panel(chr(ord("A") + i), _slot(i + 1, i + 1, 1, 1))
        for i in range(4)
    ], span=2)], aspect=(2, 3))
    _assemble_from_comic(env.project, comic, page_height_px=900, gutter_px=0)

    p1 = Image.open(env.project.pages_dir / "page_001.png").convert("RGB")
    p2 = Image.open(env.project.pages_dir / "page_002.png").convert("RGB")
    W = p1.size[0]
    # P1 left half = red (col 1), right half = green (col 2).
    assert p1.getpixel((W // 4, 450))[0] > 200
    assert p1.getpixel((3 * W // 4, 450))[1] > 200
    # P2 left half = blue (col 3), right half = yellow (col 4).
    assert p2.getpixel((W // 4, 450))[2] > 200
    assert p2.getpixel((3 * W // 4, 450))[0] > 200 and p2.getpixel((3 * W // 4, 450))[1] > 200


@_with_env
def test_spread_advances_page_numbers(env):
    # Page 0 normal, page 1 spread:2, page 3 normal — page numbering should be 1, 2, 3, 4
    env.make_panel("page_1_panel_1", 600, 900, (100, 100, 100))
    env.make_panel("page_2_panel_1", 1200, 900, (100, 100, 100))
    env.make_panel("page_3_panel_1", 600, 900, (100, 100, 100))

    comic = _comic([
        _page(0, [_panel("A", _slot(1, 1, 1, 1))]),
        _page(1, [_panel("A", _slot(1, 1, 1, 1))], span=2),
        _page(3, [_panel("A", _slot(1, 1, 1, 1))]),
    ])
    result = _assemble_from_comic(env.project, comic, page_height_px=900)
    assert set(result) == {1, 2, 3, 4}


# ---------------------------------------------------------------------------
# Missing panel placeholder
# ---------------------------------------------------------------------------


@_with_env
def test_missing_panel_renders_placeholder(env):
    # No make_panel call — source PNG absent.
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1))])])
    _assemble_from_comic(env.project, comic, page_height_px=900)
    out = Image.open(env.project.pages_dir / "page_001.png").convert("RGB")
    W, H = out.size
    # Placeholder fill is mid-grey (200, 200, 200).
    r, g, b = out.getpixel((W // 2, H // 4))  # off-centre to dodge the label text
    assert 180 < r < 220 and 180 < g < 220 and 180 < b < 220


@_with_env
def test_missing_panel_does_not_block_other_panels(env):
    env.make_panel("page_1_panel_1", 500, 500, (255, 0, 0))
    # panel 2 deliberately missing
    env.make_panel("page_1_panel_3", 500, 500, (0, 0, 255))
    env.make_panel("page_1_panel_4", 500, 500, (255, 255, 0))

    comic = _comic([_page(0, [
        _panel("A", _slot(1, 1, 1, 1)),
        _panel("B", _slot(2, 2, 1, 1)),  # missing
        _panel("C", _slot(1, 1, 2, 2)),
        _panel("D", _slot(2, 2, 2, 2)),
    ])], aspect=(2, 3))
    _assemble_from_comic(env.project, comic, page_height_px=900, gutter_px=0)

    out = Image.open(env.project.pages_dir / "page_001.png").convert("RGB")
    W, H = out.size
    # A (red) and D (yellow) should still render correctly.
    tl = out.getpixel((W // 4, H // 4))
    assert tl[0] > 200 and tl[1] < 60
    br = out.getpixel((3 * W // 4, 3 * H // 4))
    assert br[0] > 200 and br[1] > 200


# ---------------------------------------------------------------------------
# Skip / force
# ---------------------------------------------------------------------------


@_with_env
def test_skip_when_page_exists(env):
    env.make_panel("page_1_panel_1", 600, 900, (255, 0, 0))
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1))])])
    _assemble_from_comic(env.project, comic, page_height_px=900)

    # User overwrites the output.
    out_path = env.project.pages_dir / "page_001.png"
    out_path.write_bytes(b"USER_OVERRIDE")

    _assemble_from_comic(env.project, comic, page_height_px=900)  # default force=False
    assert out_path.read_bytes() == b"USER_OVERRIDE"


@_with_env
def test_force_overwrites_existing(env):
    env.make_panel("page_1_panel_1", 600, 900, (255, 0, 0))
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1))])])
    _assemble_from_comic(env.project, comic, page_height_px=900)

    out_path = env.project.pages_dir / "page_001.png"
    out_path.write_bytes(b"USER_OVERRIDE")
    _assemble_from_comic(env.project, comic, page_height_px=900, force=True)
    assert out_path.read_bytes() != b"USER_OVERRIDE"
    Image.open(out_path)  # parses


# ---------------------------------------------------------------------------
# stretch_tolerance — stretch vs cover-crop
# ---------------------------------------------------------------------------


@_with_env
def test_explicit_contain_mode_preserves_edges(env):
    # Source 800×400 (2:1), slot 600×900 (2:3). With ``fit_mode="contain"``
    # the image scales to 600×300 (min fit) and is letterboxed inside the
    # 600×900 slot with bg padding above/below. Critically, the BLUE LEFT
    # edge is preserved (cover-crop would crop it off). ``contain`` is the
    # knob to reach for when a panel can't be regenerated at slot aspect.
    img = Image.new("RGB", (800, 400), (255, 0, 0))
    for x in range(266):
        for y in range(400):
            img.putpixel((x, y), (0, 0, 255))  # blue left edge
    for x in range(533, 800):
        for y in range(400):
            img.putpixel((x, y), (0, 255, 0))  # green right edge
    img.save(env.project.panels_text_dir / "page_1_panel_1.png")

    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1))])], aspect=(2, 3))
    _assemble_from_comic(env.project, comic, page_height_px=900, fit_mode="contain")

    out = Image.open(env.project.pages_dir / "page_001.png").convert("RGB")
    # Letterbox: image occupies y=300..600 (centered), with bg padding above/below.
    # Blue left edge at x=20 within the image band.
    r, g, b = out.getpixel((20, 450))
    assert b > 200 and r < 60, f"expected blue left edge preserved, got ({r},{g},{b})"
    # Green right edge at x=580.
    r, g, b = out.getpixel((580, 450))
    assert g > 200 and r < 60, f"expected green right edge preserved, got ({r},{g},{b})"
    # Letterbox bands are bg (white).
    r, g, b = out.getpixel((300, 50))
    assert r > 240 and g > 240 and b > 240, f"expected white letterbox band, got ({r},{g},{b})"


@_with_env
def test_default_fit_mode_is_cover(env):
    # No fit_mode arg → the default. Post-§14.1 the default is ``cover``:
    # a mismatched-aspect panel is scaled to cover the slot and centre-cropped
    # (the blue/green edges slice off; the red middle survives). For generated
    # panels drift is ~0 so nothing is cropped; this exercises the fallback.
    img = Image.new("RGB", (800, 400), (255, 0, 0))
    for x in range(266):
        for y in range(400):
            img.putpixel((x, y), (0, 0, 255))
    for x in range(533, 800):
        for y in range(400):
            img.putpixel((x, y), (0, 255, 0))
    img.save(env.project.panels_text_dir / "page_1_panel_1.png")

    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1))])], aspect=(2, 3))
    _assemble_from_comic(env.project, comic, page_height_px=900)  # default fit_mode

    out = Image.open(env.project.pages_dir / "page_001.png").convert("RGB")
    # Default (cover) crops to the red middle — no white letterbox bands.
    r, g, b = out.getpixel((300, 450))
    assert r > 200 and b < 60 and g < 60, f"expected red middle after cover-crop, got ({r},{g},{b})"
    r, g, b = out.getpixel((300, 50))
    assert not (r > 240 and g > 240 and b > 240), "default cover should not letterbox"


@_with_env
def test_cover_mode_crops_edges(env):
    # Same source/slot, but fit_mode=cover. Source 800×400 covers a 600×900
    # slot by scaling to 1800×900 and centre-cropping to 600×900 — the
    # blue/green edges are sliced off; only the red middle survives.
    img = Image.new("RGB", (800, 400), (255, 0, 0))
    for x in range(266):
        for y in range(400):
            img.putpixel((x, y), (0, 0, 255))
    for x in range(533, 800):
        for y in range(400):
            img.putpixel((x, y), (0, 255, 0))
    img.save(env.project.panels_text_dir / "page_1_panel_1.png")

    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1))])], aspect=(2, 3))
    _assemble_from_comic(env.project, comic, page_height_px=900, fit_mode="cover")

    out = Image.open(env.project.pages_dir / "page_001.png").convert("RGB")
    r, g, b = out.getpixel((300, 450))
    assert r > 200 and b < 60 and g < 60, f"expected red middle after crop, got ({r},{g},{b})"


@_with_env
def test_high_tolerance_stretches(env):
    # Same source/slot mismatch, but tol=1.0 (very tolerant) → stretch.
    # Stretched: the blue/red/green bands keep their proportions, scaled to 600×900.
    img = Image.new("RGB", (800, 400), (255, 0, 0))
    for x in range(266):
        for y in range(400):
            img.putpixel((x, y), (0, 0, 255))
    for x in range(533, 800):
        for y in range(400):
            img.putpixel((x, y), (0, 255, 0))
    img.save(env.project.panels_text_dir / "page_1_panel_1.png")

    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1))])], aspect=(2, 3))
    # Drift for 2:1 source into 2:3 slot is ~2.0; tolerance must clear that.
    _assemble_from_comic(env.project, comic, page_height_px=900, stretch_tolerance=5.0)

    out = Image.open(env.project.pages_dir / "page_001.png").convert("RGB")
    # Stretched: blue band still on left.
    r, g, b = out.getpixel((50, 450))
    assert b > 200 and r < 60, f"expected blue on left after stretch, got ({r},{g},{b})"
    r, g, b = out.getpixel((550, 450))
    assert g > 200 and r < 60, f"expected green on right after stretch, got ({r},{g},{b})"


@_with_env
def test_exact_match_returns_image_unchanged_either_way(env):
    # Source 600×900 already matches the slot — both behaviours equivalent.
    env.make_panel("page_1_panel_1", 600, 900, (255, 0, 0))
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1))])], aspect=(2, 3))
    _assemble_from_comic(env.project, comic, page_height_px=900)

    out = Image.open(env.project.pages_dir / "page_001.png").convert("RGB")
    r, g, b = out.getpixel((300, 450))
    assert r > 200 and g < 60 and b < 60


# ---------------------------------------------------------------------------
# Three-tier config resolution (through the public API)
# ---------------------------------------------------------------------------


@_with_env
def test_config_supplies_page_height_when_no_arg(env):
    env.write_config("assembly:\n  page_height_px: 1500\n")
    env.make_panel("page_1_panel_1", 600, 900, (100, 100, 100))

    # Bypass the real cbml_parser via the testable seam — but still exercise
    # _resolve directly by calling the public-style resolver.
    cfg_value = assembler._resolve(None, assembler.load_config(),
                                   "assembly.page_height_px", 3000)
    assert cfg_value == 1500


@_with_env
def test_arg_overrides_config_page_height(env):
    env.write_config("assembly:\n  page_height_px: 1500\n")
    resolved = assembler._resolve(2400, assembler.load_config(),
                                  "assembly.page_height_px", 3000)
    assert resolved == 2400


@_with_env
def test_default_used_when_no_arg_no_config(env):
    # Config has no assembly key at all
    env.write_config("llm:\n  url: http://x\n")
    resolved = assembler._resolve(None, assembler.load_config(),
                                  "assembly.page_height_px", 3000)
    assert resolved == 3000


@_with_env
def test_config_supplies_all_three(env):
    env.write_config(
        "assembly:\n"
        "  page_height_px: 1500\n"
        "  gutter_px: 5\n"
        "  bg_color: \"#000000\"\n"
        "  stretch_tolerance: 0.1\n"
    )
    cfg = assembler.load_config()
    assert assembler._resolve(None, cfg, "assembly.page_height_px", 3000) == 1500
    assert assembler._resolve(None, cfg, "assembly.gutter_px", 20) == 5
    assert assembler._resolve(None, cfg, "assembly.bg_color", "#ffffff") == "#000000"
    assert abs(assembler._resolve(None, cfg, "assembly.stretch_tolerance", 0.0) - 0.1) < 1e-9


# ---------------------------------------------------------------------------
# Sanity: filename utility
# ---------------------------------------------------------------------------


def test_page_filename_zero_pads_to_three_digits():
    assert _page_filename(1) == "page_001.png"
    assert _page_filename(42) == "page_042.png"
    assert _page_filename(999) == "page_999.png"
