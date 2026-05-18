"""Tests for lazycomics.enricher.

The real CBML parser is a git dependency that isn't always installed in
the dev sandbox. Tests construct mock Comic objects (SimpleNamespace) that
match the parser's API shape and exercise ``_enrich_from_comic`` directly.
The public ``enrich()`` just parses + delegates; its parse step doesn't
need separate coverage here.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics.asset_registry import register_character, register_location  # noqa: E402
from lazycomics.enricher import _enrich_from_comic  # noqa: E402
from lazycomics.models import CaptionBox, CharacterRef, DialogueLine, Sfx  # noqa: E402
from lazycomics.project import create_project  # noqa: E402


# ---------------------------------------------------------------------------
# Mock builders — match the cbml_parser API shape
# ---------------------------------------------------------------------------


def _slot(c1, c2, r1, r2):
    return SimpleNamespace(cols=(c1, c2), rows=(r1, r2))


def _panel(label, slot, loc, chars=(), shot=None, mood=None, action=None,
           dialogue=(), captions=(), sfx=()):
    return SimpleNamespace(
        label=label, slot=slot, loc=loc, chars=list(chars),
        shot=shot, mood=mood, action=action,
        dialogue=list(dialogue), captions=list(captions),
        sfx=list(sfx),
    )


def _page(index, panels, span=1):
    return SimpleNamespace(index=index, panels=list(panels), span=span)


def _comic(pages, aspect=(2, 3)):
    return SimpleNamespace(
        title="Mock", metadata={}, warnings=[],
        pages=list(pages), aspect=aspect,
    )


def _dl(character, text, bubble_type="speech"):
    return SimpleNamespace(character=character, text=text, bubble_type=bubble_type)


def _cap(text, bg="#000000", color="#ffffff", pos="top-left"):
    return SimpleNamespace(text=text, bg=bg, color=color, pos=pos)


def _sfx(text, color="#000000", pos="center"):
    return SimpleNamespace(text=text, color=color, pos=pos)


# ---------------------------------------------------------------------------
# Test environment: tmp project
# ---------------------------------------------------------------------------


class _Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cbml = self.root / "story.cbml"
        cbml.write_text("## Test\naspect: 2:3\n\nPAGE preset:splash\n\nPANEL A\nloc: room\n> A test.\n")
        self.project = create_project("p1", cbml, base_dir=self.root)

    def close(self):
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
# Basics: panel_id, file output, page/panel numbering
# ---------------------------------------------------------------------------


@_with_env
def test_single_panel_writes_one_json_with_correct_name(env):
    comic = _comic([
        _page(0, [_panel("A", _slot(1, 1, 1, 1), loc="room",
                         action="A test scene.")]),
    ])
    panels = _enrich_from_comic(env.project, comic)

    assert len(panels) == 1
    assert panels[0].panel_id == "page_1_panel_1"
    assert (env.project.enriched_dir / "page_1_panel_1.json").is_file()


@_with_env
def test_two_pages_two_panels_each_writes_four_files(env):
    p1 = _page(0, [
        _panel("A", _slot(1, 1, 1, 1), loc="room"),
        _panel("B", _slot(2, 2, 1, 1), loc="room"),
    ])
    p2 = _page(1, [
        _panel("A", _slot(1, 1, 1, 1), loc="hall"),
        _panel("B", _slot(2, 2, 1, 1), loc="hall"),
    ])
    panels = _enrich_from_comic(env.project, _comic([p1, p2]))

    assert [p.panel_id for p in panels] == [
        "page_1_panel_1", "page_1_panel_2",
        "page_2_panel_1", "page_2_panel_2",
    ]
    for pid in (p.panel_id for p in panels):
        assert (env.project.enriched_dir / f"{pid}.json").is_file()


@_with_env
def test_page_and_panel_indices_are_zero_based_in_data(env):
    """0-based in the dataclass, 1-based in filenames/IDs (human-readable)."""
    comic = _comic([
        _page(0, [_panel("A", _slot(1, 1, 1, 1), loc="room")]),
        _page(1, [_panel("A", _slot(1, 1, 1, 1), loc="room")]),
    ])
    panels = _enrich_from_comic(env.project, comic)
    assert panels[0].page_index == 0
    assert panels[0].panel_index == 0
    assert panels[1].page_index == 1
    assert panels[1].panel_index == 0


# ---------------------------------------------------------------------------
# Passthroughs: action, shot, mood, dialogue
# ---------------------------------------------------------------------------


@_with_env
def test_passthrough_action_shot_mood(env):
    comic = _comic([
        _page(0, [_panel("A", _slot(1, 1, 1, 1),
                         loc="room",
                         action="She turns and runs.",
                         shot="wide tracking shot",
                         mood="urgent")])
    ])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.action == "She turns and runs."
    assert panel.shot_hint == "wide tracking shot"
    assert panel.mood == "urgent"


@_with_env
def test_missing_shot_and_mood_default_safely(env):
    # CBML allows shot/mood to be absent (parser returns None).
    comic = _comic([
        _page(0, [_panel("A", _slot(1, 1, 1, 1), loc="room",
                         shot=None, mood=None, action=None)])
    ])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.shot_hint == ""
    assert panel.mood is None
    assert panel.action == ""


@_with_env
def test_dialogue_lines_converted(env):
    comic = _comic([
        _page(0, [_panel("A", _slot(1, 1, 1, 1), loc="room",
                         chars=["NOVA"],
                         dialogue=[
                             _dl("NOVA", "Stay back.", "speech"),
                             _dl("NOVA", "Don't move.", "whisper"),
                         ])])
    ])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert len(panel.dialogue_lines) == 2
    assert all(isinstance(d, DialogueLine) for d in panel.dialogue_lines)
    assert panel.dialogue_lines[0].character == "NOVA"
    assert panel.dialogue_lines[0].text == "Stay back."
    assert panel.dialogue_lines[1].bubble_type == "whisper"


@_with_env
def test_captions_renamed_fields(env):
    """Parser uses bg/color/pos; ours uses bg_color/text_color/position."""
    comic = _comic([
        _page(0, [_panel("A", _slot(1, 1, 1, 1), loc="room",
                         captions=[_cap("2:17 AM.",
                                        bg="#0a0f14",
                                        color="#a0c8d8",
                                        pos="top-left")])])
    ])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert len(panel.caption_boxes) == 1
    cap = panel.caption_boxes[0]
    assert isinstance(cap, CaptionBox)
    assert cap.text == "2:17 AM."
    assert cap.bg_color == "#0a0f14"
    assert cap.text_color == "#a0c8d8"
    assert cap.position == "top-left"


@_with_env
def test_sfx_converted_with_field_rename(env):
    """Parser Sfx has (text, color, pos); ours has (text, color, position)."""
    comic = _comic([
        _page(0, [_panel("A", _slot(1, 1, 1, 1), loc="room",
                         sfx=[_sfx("BOOM!", color="#ff2200", pos="top-right"),
                              _sfx("crash...", color="#000000", pos="center")])])
    ])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert len(panel.sfx_lines) == 2
    assert all(isinstance(s, Sfx) for s in panel.sfx_lines)
    assert panel.sfx_lines[0].text == "BOOM!"
    assert panel.sfx_lines[0].color == "#ff2200"
    assert panel.sfx_lines[0].position == "top-right"
    assert panel.sfx_lines[1].position == "center"


@_with_env
def test_sfx_empty_when_no_sfx_in_cbml(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="room")])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.sfx_lines == []


@_with_env
def test_sfx_persisted_in_json(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="room",
                                     sfx=[_sfx("BOOM!", pos="center")])])])
    _enrich_from_comic(env.project, comic)
    data = json.loads((env.project.enriched_dir / "page_1_panel_1.json").read_text())
    assert data["sfx_lines"] == [{"text": "BOOM!", "color": "#000000", "position": "center"}]


# ---------------------------------------------------------------------------
# Loc split rule (§12)
# ---------------------------------------------------------------------------


@_with_env
def test_loc_no_spaces_is_identifier(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="warehouse_night")])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.loc_identifier == "warehouse_night"
    assert panel.loc_description == "warehouse night"  # humanised default


@_with_env
def test_loc_with_spaces_is_freetext(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1),
                                     loc="a rain-soaked harbour at dawn")])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.loc_identifier is None
    assert panel.loc_description == "a rain-soaked harbour at dawn"


@_with_env
def test_loc_identifier_resolves_from_registry(env):
    register_location(env.project, "warehouse_night",
                      description="dark industrial warehouse, neon leak")
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="warehouse_night")])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.loc_identifier == "warehouse_night"
    assert panel.loc_description == "dark industrial warehouse, neon leak"  # registry wins


@_with_env
def test_loc_identifier_unregistered_falls_back_to_humanised(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="back_alley_dusk")])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.loc_identifier == "back_alley_dusk"
    assert panel.loc_description == "back alley dusk"


# ---------------------------------------------------------------------------
# Character lookup
# ---------------------------------------------------------------------------


@_with_env
def test_characters_resolved_from_registry(env):
    register_character(env.project, "NOVA",
                       description="young woman, blue hair, leather jacket")
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1),
                                     loc="room", chars=["NOVA"])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert len(panel.characters) == 1
    assert isinstance(panel.characters[0], CharacterRef)
    assert panel.characters[0].identifier == "NOVA"
    assert panel.characters[0].visual_description == "young woman, blue hair, leather jacket"


@_with_env
def test_unregistered_character_falls_back_to_minimal_ref(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1),
                                     loc="room", chars=["GHOST"])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert len(panel.characters) == 1
    assert panel.characters[0].identifier == "GHOST"
    assert panel.characters[0].visual_description == ""
    assert panel.characters[0].lora is None


# ---------------------------------------------------------------------------
# Bleed edges (§12)
# ---------------------------------------------------------------------------


@_with_env
def test_bleed_splash_has_all_four_edges(env):
    # Splash is 1x1 — touches every edge.
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x")])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.bleed_edges == {"top", "right", "bottom", "left"}


@_with_env
def test_bleed_corner_panel_in_2x2(env):
    # 2x2 grid; top-left panel A bleeds top + left only.
    p = _page(0, [
        _panel("A", _slot(1, 1, 1, 1), loc="x"),
        _panel("B", _slot(2, 2, 1, 1), loc="x"),
        _panel("C", _slot(1, 1, 2, 2), loc="x"),
        _panel("D", _slot(2, 2, 2, 2), loc="x"),
    ])
    panels = _enrich_from_comic(env.project, _comic([p]))
    edges = {p.panel_id: p.bleed_edges for p in panels}
    assert edges["page_1_panel_1"] == {"top", "left"}
    assert edges["page_1_panel_2"] == {"top", "right"}
    assert edges["page_1_panel_3"] == {"bottom", "left"}
    assert edges["page_1_panel_4"] == {"bottom", "right"}


@_with_env
def test_bleed_interior_panel_has_no_edges(env):
    # 3x3 grid; centre panel touches nothing.
    p = _page(0, [_panel("X", _slot(2, 2, 2, 2), loc="x")])
    # Add a satellite to push max_col / max_row to 3.
    p.panels.append(_panel("Y", _slot(3, 3, 3, 3), loc="x"))
    panels = _enrich_from_comic(env.project, _comic([p]))
    assert panels[0].bleed_edges == set()  # centre panel
    assert panels[1].bleed_edges == {"right", "bottom"}


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


@_with_env
def test_json_set_serialised_as_sorted_list(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x")])])
    _enrich_from_comic(env.project, comic)

    with (env.project.enriched_dir / "page_1_panel_1.json").open() as f:
        data = json.load(f)
    # bleed_edges is set in-memory, sorted list on disk.
    assert data["bleed_edges"] == ["bottom", "left", "right", "top"]


@_with_env
def test_json_path_serialised_as_string(env):
    # Give the location a reference image so loc_reference_images is non-empty.
    img = env.root / "loc.png"
    img.write_bytes(b"fake")
    register_location(env.project, "warehouse",
                      description="dark", reference_images=[img])

    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="warehouse")])])
    _enrich_from_comic(env.project, comic)

    with (env.project.enriched_dir / "page_1_panel_1.json").open() as f:
        data = json.load(f)
    assert len(data["loc_reference_images"]) == 1
    assert isinstance(data["loc_reference_images"][0], str)
    assert data["loc_reference_images"][0].endswith("loc.png")


@_with_env
def test_json_unicode_dialogue_preserved(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="room",
                                     chars=["NOVA"],
                                     dialogue=[_dl("NOVA", "¡Hola! 漢字 🚀")])])])
    _enrich_from_comic(env.project, comic)

    with (env.project.enriched_dir / "page_1_panel_1.json").open(encoding="utf-8") as f:
        data = json.load(f)
    assert data["dialogue_lines"][0]["text"] == "¡Hola! 漢字 🚀"


@_with_env
def test_json_full_panel_contains_all_filled_fields(env):
    register_character(env.project, "NOVA", description="blue hair")
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1),
                                     loc="warehouse_night",
                                     chars=["NOVA"],
                                     action="She moves.",
                                     shot="closeup",
                                     mood="tense",
                                     dialogue=[_dl("NOVA", "Now.")],
                                     captions=[_cap("Later.")])])])
    _enrich_from_comic(env.project, comic)

    with (env.project.enriched_dir / "page_1_panel_1.json").open() as f:
        data = json.load(f)

    assert data["panel_id"] == "page_1_panel_1"
    assert data["action"] == "She moves."
    assert data["shot_hint"] == "closeup"
    assert data["mood"] == "tense"
    assert data["loc_identifier"] == "warehouse_night"
    assert data["characters"][0]["identifier"] == "NOVA"
    assert data["dialogue_lines"][0]["character"] == "NOVA"
    assert data["caption_boxes"][0]["text"] == "Later."


# ---------------------------------------------------------------------------
# Aspect ratio — derived from comic.aspect, page.span, grid, slot
# ---------------------------------------------------------------------------


def _approx(actual, expected, tol=1e-9):
    return abs(actual - expected) < tol


@_with_env
def test_aspect_splash_on_2_3(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x")])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert _approx(panel.aspect_ratio, 2 / 3)


@_with_env
def test_aspect_2x2_grid_matches_page_aspect(env):
    # In a 2:3 page with uniform 2x2 grid, each cell is also 2:3.
    comic = _comic([_page(0, [
        _panel("A", _slot(1, 1, 1, 1), loc="x"),
        _panel("B", _slot(2, 2, 1, 1), loc="x"),
        _panel("C", _slot(1, 1, 2, 2), loc="x"),
        _panel("D", _slot(2, 2, 2, 2), loc="x"),
    ])])
    panels = _enrich_from_comic(env.project, comic)
    for p in panels:
        assert _approx(p.aspect_ratio, 2 / 3)


@_with_env
def test_aspect_wide_panel_in_3x2_grid(env):
    # 2:3 page, 3 cols × 2 rows, panel spanning cols 1-2:
    # canvas 2×3, cell 2/3 × 3/2, panel 2 cells wide → 4/3 × 3/2 → 8/9
    comic = _comic([
        _page(0, [
            _panel("A", _slot(1, 2, 1, 1), loc="x"),
            _panel("B", _slot(3, 3, 1, 1), loc="x"),
            _panel("C", _slot(1, 1, 2, 2), loc="x"),
            _panel("D", _slot(2, 3, 2, 2), loc="x"),
        ]),
    ])
    panels = _enrich_from_comic(env.project, comic)
    assert _approx(panels[0].aspect_ratio, 8 / 9)  # wide panel
    assert _approx(panels[1].aspect_ratio, 4 / 9)  # tall single-column


@_with_env
def test_aspect_full_bleed_spread(env):
    # spread:2 with default 1×1 grid on 2:3 → canvas 4:3
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x")], span=2)])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert _approx(panel.aspect_ratio, 4 / 3)


@_with_env
def test_aspect_spread_with_2x1_grid_panels_match_page(env):
    # spread:2 grid:2x1 on 2:3 → each half is one page's worth → 2:3 again
    comic = _comic([_page(0, [
        _panel("A", _slot(1, 1, 1, 1), loc="x"),
        _panel("B", _slot(2, 2, 1, 1), loc="x"),
    ], span=2)])
    panels = _enrich_from_comic(env.project, comic)
    for p in panels:
        assert _approx(p.aspect_ratio, 2 / 3)


@_with_env
def test_aspect_spread_with_4x1_grid_narrow_panels(env):
    # spread:2 grid:4x1 on 2:3 → canvas 4:3, each panel = 1/4 wide = 1×3 → 1:3
    comic = _comic([_page(0, [
        _panel("A", _slot(1, 1, 1, 1), loc="x"),
        _panel("B", _slot(2, 2, 1, 1), loc="x"),
        _panel("C", _slot(3, 3, 1, 1), loc="x"),
        _panel("D", _slot(4, 4, 1, 1), loc="x"),
    ], span=2)])
    panels = _enrich_from_comic(env.project, comic)
    for p in panels:
        assert _approx(p.aspect_ratio, 1 / 3)


@_with_env
def test_aspect_non_default_page_aspect(env):
    # 1:1 (square) page, 2x2 grid → each cell is 1:1
    comic = _comic([_page(0, [
        _panel("A", _slot(1, 1, 1, 1), loc="x"),
        _panel("B", _slot(2, 2, 1, 1), loc="x"),
        _panel("C", _slot(1, 1, 2, 2), loc="x"),
        _panel("D", _slot(2, 2, 2, 2), loc="x"),
    ])], aspect=(1, 1))
    panels = _enrich_from_comic(env.project, comic)
    for p in panels:
        assert _approx(p.aspect_ratio, 1.0)


@_with_env
def test_aspect_persisted_in_json(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x")])])
    _enrich_from_comic(env.project, comic)
    data = json.loads((env.project.enriched_dir / "page_1_panel_1.json").read_text())
    assert _approx(data["aspect_ratio"], 2 / 3)


# ---------------------------------------------------------------------------
# bubble_layout — per-character planning regions for text_renderer / inpaint
# ---------------------------------------------------------------------------


@_with_env
def test_bubble_layout_empty_when_no_chars(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x", chars=[])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.bubble_layout == []


@_with_env
def test_bubble_layout_one_char_covers_whole_panel(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["NOVA"])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert len(panel.bubble_layout) == 1
    region = panel.bubble_layout[0]
    assert region.character == "NOVA"
    assert _approx(region.x_frac, 0.0)
    assert _approx(region.y_frac, 0.0)
    assert _approx(region.width_frac, 1.0)
    assert _approx(region.height_frac, 1.0)


@_with_env
def test_bubble_layout_two_chars_left_right_halves(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["NOVA", "REX"])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert len(panel.bubble_layout) == 2
    left, right = panel.bubble_layout
    assert left.character == "NOVA"
    assert _approx(left.x_frac, 0.0)
    assert _approx(left.width_frac, 0.5)
    assert _approx(left.height_frac, 1.0)
    assert right.character == "REX"
    assert _approx(right.x_frac, 0.5)
    assert _approx(right.width_frac, 0.5)


@_with_env
def test_bubble_layout_three_chars_thirds(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["A", "B", "C"])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert len(panel.bubble_layout) == 3
    expected_width = 1 / 3
    for i, region in enumerate(panel.bubble_layout):
        assert _approx(region.x_frac, i * expected_width)
        assert _approx(region.width_frac, expected_width)
        assert _approx(region.height_frac, 1.0)


@_with_env
def test_bubble_layout_preserves_char_order(env):
    """Region order matches the CBML chars: list (parser preserves authoring order)."""
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["REX", "NOVA", "GHOST"])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert [r.character for r in panel.bubble_layout] == ["REX", "NOVA", "GHOST"]


@_with_env
def test_bubble_layout_persisted_in_json(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["NOVA", "REX"])])])
    _enrich_from_comic(env.project, comic)
    data = json.loads((env.project.enriched_dir / "page_1_panel_1.json").read_text())
    assert len(data["bubble_layout"]) == 2
    assert data["bubble_layout"][0]["character"] == "NOVA"
    assert _approx(data["bubble_layout"][0]["x_frac"], 0.0)
    assert _approx(data["bubble_layout"][1]["x_frac"], 0.5)


# ---------------------------------------------------------------------------
# char_generation_strategy — single vs multi_inpaint
# ---------------------------------------------------------------------------


@_with_env
def test_strategy_single_when_no_chars(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x", chars=[])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.char_generation_strategy == "single"


@_with_env
def test_strategy_single_when_one_char(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["NOVA"])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.char_generation_strategy == "single"


@_with_env
def test_strategy_multi_inpaint_when_two_chars(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["NOVA", "REX"])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.char_generation_strategy == "multi_inpaint"


@_with_env
def test_strategy_multi_inpaint_when_three_chars(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["NOVA", "REX", "GHOST"])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.char_generation_strategy == "multi_inpaint"


# ---------------------------------------------------------------------------
# primary_character — shot-hint match, dialogue-count fallback, first-listed
# ---------------------------------------------------------------------------


@_with_env
def test_primary_none_when_no_chars(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x", chars=[])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.primary_character is None


@_with_env
def test_primary_is_sole_char_when_one(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["NOVA"])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.primary_character == "NOVA"


@_with_env
def test_primary_from_shot_hint_regex(env):
    """shot: 'closeup on NOVA' names NOVA → primary, regardless of dialogue."""
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["REX", "NOVA"],
                                     shot="closeup on NOVA",
                                     dialogue=[
                                         _dl("REX", "Line 1."),
                                         _dl("REX", "Line 2."),
                                         _dl("REX", "Line 3."),
                                     ])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.primary_character == "NOVA"


@_with_env
def test_primary_from_shot_hint_case_insensitive(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["NOVA", "REX"],
                                     shot="medium shot of rex")])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.primary_character == "REX"


@_with_env
def test_primary_fallback_to_most_dialogue(env):
    """No shot-hint match → most dialogue lines wins."""
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["NOVA", "REX"],
                                     dialogue=[
                                         _dl("NOVA", "One."),
                                         _dl("REX", "Two."),
                                         _dl("REX", "Three."),
                                     ])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.primary_character == "REX"


@_with_env
def test_primary_fallback_to_first_listed_no_dialogue(env):
    """No shot match, no dialogue → first char in CBML authoring order."""
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["REX", "NOVA"])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.primary_character == "REX"


@_with_env
def test_primary_dialogue_tie_favours_first_listed(env):
    """Equal dialogue counts → first in chars: order wins."""
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["NOVA", "REX"],
                                     dialogue=[
                                         _dl("NOVA", "Same."),
                                         _dl("REX", "Count."),
                                     ])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.primary_character == "NOVA"


# ---------------------------------------------------------------------------
# inpaint_order — primary first, rest by dialogue count descending
# ---------------------------------------------------------------------------


@_with_env
def test_inpaint_order_empty_when_no_chars(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x", chars=[])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.inpaint_order == []


@_with_env
def test_inpaint_order_single_char(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["NOVA"])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.inpaint_order == ["NOVA"]


@_with_env
def test_inpaint_order_primary_first(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["NOVA", "REX"],
                                     shot="closeup on REX")])])
    panel = _enrich_from_comic(env.project, comic)[0]
    assert panel.inpaint_order[0] == "REX"
    assert panel.inpaint_order == ["REX", "NOVA"]


@_with_env
def test_inpaint_order_rest_sorted_by_dialogue_count(env):
    """After the primary, remaining chars sorted by dialogue lines descending."""
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["NOVA", "REX", "GHOST"],
                                     shot="closeup on NOVA",
                                     dialogue=[
                                         _dl("REX", "A."),
                                         _dl("GHOST", "B."),
                                         _dl("GHOST", "C."),
                                         _dl("GHOST", "D."),
                                         _dl("REX", "E."),
                                     ])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    # NOVA first (primary via shot), then GHOST (3 lines), then REX (2 lines)
    assert panel.inpaint_order == ["NOVA", "GHOST", "REX"]


@_with_env
def test_inpaint_order_rest_stable_on_equal_dialogue(env):
    """Equal dialogue count → preserves original chars: order among non-primary."""
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["NOVA", "REX", "GHOST"],
                                     dialogue=[
                                         _dl("NOVA", "Line."),
                                     ])])])
    panel = _enrich_from_comic(env.project, comic)[0]
    # NOVA primary (most dialogue), REX & GHOST tied at 0 → original order
    assert panel.inpaint_order == ["NOVA", "REX", "GHOST"]


@_with_env
def test_strategy_and_order_persisted_in_json(env):
    comic = _comic([_page(0, [_panel("A", _slot(1, 1, 1, 1), loc="x",
                                     chars=["NOVA", "REX"],
                                     dialogue=[_dl("REX", "Hello.")])])])
    _enrich_from_comic(env.project, comic)
    data = json.loads((env.project.enriched_dir / "page_1_panel_1.json").read_text())
    assert data["char_generation_strategy"] == "multi_inpaint"
    assert data["primary_character"] == "REX"
    assert data["inpaint_order"] == ["REX", "NOVA"]
