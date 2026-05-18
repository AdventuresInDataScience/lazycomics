"""Tests for lazycomics.models.

Dataclasses have no behaviour — these tests just verify each constructs
with sensible defaults and that mutable defaults are independent across
instances (a real Python gotcha worth guarding against).
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics.models import (  # noqa: E402
    BubbleRegion,
    CaptionBox,
    CharacterRef,
    DialogueLine,
    LocationAsset,
    LoRAConfig,
    PageLayout,
    PanelGenerationRequest,
    Project,
    StyleAsset,
)


def test_lora_config_required_args_only():
    lora = LoRAConfig(path=Path("/x/y.safetensors"), weight=0.7)
    assert lora.trigger_word is None


def test_dialogue_line_default_bubble_type():
    d = DialogueLine(character="NOVA", text="hello")
    assert d.bubble_type == "speech"


def test_caption_box_defaults():
    c = CaptionBox(text="Later that night...")
    assert c.position == "top-left"


def test_bubble_region_required_args():
    b = BubbleRegion(character="X", x_frac=0.1, y_frac=0.1, width_frac=0.3, height_frac=0.2)
    assert b.character == "X"


def test_character_ref_defaults():
    c = CharacterRef(identifier="NOVA")
    assert c.visual_description == ""
    assert c.reference_images == []
    assert c.lora is None


def test_style_asset_defaults():
    s = StyleAsset()
    assert s.preset == "custom"
    assert s.reference_images == []


def test_location_asset_defaults():
    loc = LocationAsset(identifier="warehouse_night")
    assert loc.description == ""


def test_panel_generation_request_defaults():
    p = PanelGenerationRequest(panel_id="p1", page_index=0, panel_index=0)
    assert p.bleed_edges == set()
    assert p.characters == []
    assert p.dialogue_lines == []
    assert p.composition_flags == {}
    assert p.char_generation_strategy == "single"
    assert p.pixel_rect == (0, 0, 0, 0)


def test_panel_default_collections_are_independent():
    # Regression guard: shared mutable defaults would cross-contaminate.
    a = PanelGenerationRequest(panel_id="a", page_index=0, panel_index=0)
    b = PanelGenerationRequest(panel_id="b", page_index=0, panel_index=1)
    a.characters.append(CharacterRef(identifier="NOVA"))
    assert b.characters == []


def test_page_layout_defaults():
    p = PageLayout(page_index=0, grid_cols=2, grid_rows=2, page_width_px=1988, page_height_px=3075)
    assert p.gutter_px == 20
    assert p.panels == []


def test_project_constructs():
    # Project is just a paths bundle; verify it accepts all 13 fields.
    Project(
        name="demo",
        base_dir=Path("/tmp/demo"),
        cbml_path=Path("/tmp/demo/source.cbml"),
        assets_dir=Path("/tmp/demo/assets"),
        enriched_dir=Path("/tmp/demo/enriched"),
        prompts_dir=Path("/tmp/demo/prompts"),
        refs_prepared_dir=Path("/tmp/demo/refs_prepared"),
        panels_dir=Path("/tmp/demo/panels"),
        panels_text_dir=Path("/tmp/demo/panels_text"),
        pages_dir=Path("/tmp/demo/pages"),
        pages_upscaled_dir=Path("/tmp/demo/pages_upscaled"),
        output_dir=Path("/tmp/demo/output"),
        manifest_path=Path("/tmp/demo/manifest.json"),
    )
