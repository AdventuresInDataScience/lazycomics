"""Tests for lazycomics.prompt_builder."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics.asset_registry import register_style, set_lora  # noqa: E402
from lazycomics.prompt_builder import (  # noqa: E402
    DEFAULT_NEGATIVE_PROMPT,
    build_prompts,
)
from lazycomics.project import create_project  # noqa: E402


# ---------------------------------------------------------------------------
# Test environment
# ---------------------------------------------------------------------------


class _Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cbml = self.root / "story.cbml"
        cbml.write_text("## Test\naspect: 2:3\n\nPAGE preset:splash\n\nPANEL A\nloc: room\n> A test.\n")
        self.project = create_project("p1", cbml, base_dir=self.root)

        # Stand-in LoRA file for tests that register one.
        self.lora = self.root / "style.safetensors"
        self.lora.write_bytes(b"fake")

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


def _write_enriched(project, panel_id, **overrides):
    """Write a minimal enriched JSON file with defaults filled in."""
    data = {
        "panel_id": panel_id,
        "page_index": 0,
        "panel_index": 0,
        "aspect_ratio": 2 / 3,
        "action": "",
        "shot_hint": "",
        "mood": None,
        "loc_identifier": None,
        "loc_description": "",
        "characters": [],
        "dialogue_lines": [],
        "caption_boxes": [],
    }
    data.update(overrides)
    (project.enriched_dir / f"{panel_id}.json").write_text(json.dumps(data))


def _char(identifier, visual_description="", lora=None):
    return {
        "identifier": identifier,
        "visual_description": visual_description,
        "reference_images": [],
        "lora": lora,
    }


def _lora(trigger_word, path="/fake/x.safetensors", weight=0.7):
    return {"path": path, "weight": weight, "trigger_word": trigger_word}


# ---------------------------------------------------------------------------
# Files + return value
# ---------------------------------------------------------------------------


@_with_env
def test_writes_pos_and_neg_files(env):
    _write_enriched(env.project, "page_1_panel_1", action="A test scene")
    build_prompts(env.project)
    assert (env.project.prompts_dir / "page_1_panel_1.txt").is_file()
    assert (env.project.prompts_dir / "page_1_panel_1.neg.txt").is_file()


@_with_env
def test_returns_dict_panel_id_to_pair(env):
    _write_enriched(env.project, "p1", action="Test action")
    result = build_prompts(env.project)
    assert "p1" in result
    pos, neg = result["p1"]
    assert "Test action" in pos
    assert neg == DEFAULT_NEGATIVE_PROMPT


@_with_env
def test_multiple_panels_all_written(env):
    _write_enriched(env.project, "page_1_panel_1", action="first")
    _write_enriched(env.project, "page_1_panel_2", action="second")
    result = build_prompts(env.project)
    assert set(result) == {"page_1_panel_1", "page_1_panel_2"}
    assert "first" in result["page_1_panel_1"][0]
    assert "second" in result["page_1_panel_2"][0]


@_with_env
def test_no_enriched_files_returns_empty(env):
    assert build_prompts(env.project) == {}


# ---------------------------------------------------------------------------
# Per-field inclusion
# ---------------------------------------------------------------------------


@_with_env
def test_action_included(env):
    _write_enriched(env.project, "p1", action="Nova sprints down the alley")
    build_prompts(env.project)
    text = (env.project.prompts_dir / "p1.txt").read_text()
    assert "Nova sprints down the alley" in text


@_with_env
def test_shot_and_mood_included(env):
    _write_enriched(env.project, "p1", shot_hint="wide low angle", mood="urgent, tense")
    build_prompts(env.project)
    text = (env.project.prompts_dir / "p1.txt").read_text()
    assert "wide low angle" in text
    assert "urgent" in text


@_with_env
def test_loc_description_prefixed_setting(env):
    _write_enriched(env.project, "p1", loc_description="rain-slicked neon alley")
    build_prompts(env.project)
    text = (env.project.prompts_dir / "p1.txt").read_text()
    assert "setting: rain-slicked neon alley" in text


@_with_env
def test_empty_panel_produces_empty_prompt(env):
    _write_enriched(env.project, "p1")  # all defaults, no style registered
    build_prompts(env.project)
    text = (env.project.prompts_dir / "p1.txt").read_text().strip()
    assert text == ""


# ---------------------------------------------------------------------------
# Style handling
# ---------------------------------------------------------------------------


@_with_env
def test_signage_in_setting_warns_once_per_location(env):
    # Problem 7: signs come out as gibberish lettering, so say so up front.
    for pid in ("p1", "p2"):
        _write_enriched(env.project, pid, loc_identifier="BAR",
                        loc_description="dim bar, neon signs glowing")
    _write_enriched(env.project, "p3", loc_identifier="ALLEY",
                    loc_description="rain-slicked alley, abstract neon light shapes",
                    shot_hint="wide shot past a billboard")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        build_prompts(env.project)
    warnings = [line for line in out.getvalue().splitlines() if line.startswith("[prompts]")]
    assert len(warnings) == 2
    assert "BAR: the setting" in warnings[0] and "'signs'" in warnings[0]
    assert "p3: the shot" in warnings[1] and "'billboard'" in warnings[1]


@_with_env
def test_style_prefix_included(env):
    register_style(env.project, prompt_prefix="silver age comic art, bold inks")
    _write_enriched(env.project, "p1", action="x")
    build_prompts(env.project)
    text = (env.project.prompts_dir / "p1.txt").read_text()
    assert "silver age comic art, bold inks" in text


@_with_env
def test_style_lora_trigger_included(env):
    register_style(env.project, prompt_prefix="silver", lora_path=env.lora)
    set_lora(env.project, "style", None, env.lora, trigger_word="silver_v1")
    _write_enriched(env.project, "p1", action="x")
    build_prompts(env.project)
    text = (env.project.prompts_dir / "p1.txt").read_text()
    assert "silver_v1" in text


@_with_env
def test_no_style_registered_is_fine(env):
    _write_enriched(env.project, "p1", action="Just an action")
    build_prompts(env.project)  # must not raise
    text = (env.project.prompts_dir / "p1.txt").read_text()
    assert "Just an action" in text


# ---------------------------------------------------------------------------
# Character handling
# ---------------------------------------------------------------------------


@_with_env
def test_character_trigger_and_description_both_emitted_when_lora_present(env):
    # LoRA pins identity; description carries clothing/state the LoRA typically
    # doesn't capture. Both belong in the prompt.
    _write_enriched(env.project, "p1",
        characters=[_char("NOVA", visual_description="blue hair",
                          lora=_lora("nova_v1"))],
        action="x",
    )
    build_prompts(env.project)
    text = (env.project.prompts_dir / "p1.txt").read_text()
    assert "nova_v1" in text
    assert "blue hair" in text


@_with_env
def test_character_description_used_when_no_lora(env):
    _write_enriched(env.project, "p1",
        characters=[_char("NOVA", visual_description="blue hair, leather jacket")],
        action="x",
    )
    build_prompts(env.project)
    text = (env.project.prompts_dir / "p1.txt").read_text()
    # Labelled, so actions and the group cue that name characters bind to it.
    assert "Nova (blue hair, leather jacket)" in text


@_with_env
def test_character_identifier_used_when_no_lora_and_no_description(env):
    _write_enriched(env.project, "p1",
        characters=[_char("NOVA")],
        action="x",
    )
    build_prompts(env.project)
    text = (env.project.prompts_dir / "p1.txt").read_text()
    assert "NOVA" in text


@_with_env
def test_multiple_characters_each_emitted(env):
    _write_enriched(env.project, "p1",
        characters=[
            _char("NOVA", lora=_lora("nova_v1")),
            _char("REX", visual_description="bald, scarred"),
        ],
        action="They face off",
    )
    build_prompts(env.project)
    text = (env.project.prompts_dir / "p1.txt").read_text()
    assert "nova_v1" in text
    assert "bald, scarred" in text
    assert "They face off" in text


def _line(character, text="Hello there.", bubble_type="speech"):
    return {"character": character, "text": text, "bubble_type": bubble_type}


@_with_env
def test_group_cue_names_three_or_more_characters_in_order(env):
    # Problem 16: group shots whose tails must tell speakers apart name
    # everyone, in bubble_layout (CBML) order.
    _write_enriched(env.project, "p1",
        characters=[_char("NOVA"), _char("REX"), _char("OLD_MAN")],
        dialogue_lines=[_line("REX"), _line("OLD_MAN", bubble_type="thought")],
        action="They wait",
    )
    build_prompts(env.project)
    text = (env.project.prompts_dir / "p1.txt").read_text()
    cue = "three people, left to right: Nova, Rex and Old Man, each clearly visible"
    assert cue in text
    assert text.index(cue) < text.index("They wait")


@_with_env
def test_no_group_cue_for_two_characters(env):
    _write_enriched(env.project, "p1",
        characters=[_char("NOVA"), _char("REX")],
        dialogue_lines=[_line("NOVA"), _line("REX")],
        action="They wait",
    )
    build_prompts(env.project)
    assert "left to right" not in (env.project.prompts_dir / "p1.txt").read_text()


@_with_env
def test_no_group_cue_unless_two_of_the_cast_speak(env):
    # The order only matters to tails, so a panel with under two speakers
    # among its cast (a narrator isn't one) gets no cue.
    chars = [_char("NOVA"), _char("REX"), _char("OLD_MAN")]
    _write_enriched(env.project, "p1", characters=chars, action="They wait")
    _write_enriched(env.project, "p2", characters=chars, action="They wait",
                    dialogue_lines=[_line("REX"), _line("REX", "Again."), _line("NARRATOR")])
    build_prompts(env.project)
    for pid in ("p1", "p2"):
        assert "left to right" not in (env.project.prompts_dir / f"{pid}.txt").read_text()


# ---------------------------------------------------------------------------
# Negative prompt
# ---------------------------------------------------------------------------


@_with_env
def test_negative_prompt_is_default(env):
    _write_enriched(env.project, "p1", action="x")
    build_prompts(env.project)
    neg = (env.project.prompts_dir / "p1.neg.txt").read_text().strip()
    assert neg == DEFAULT_NEGATIVE_PROMPT


# ---------------------------------------------------------------------------
# Full integration: every field populated
# ---------------------------------------------------------------------------


@_with_env
def test_full_panel_assembles_in_expected_order(env):
    register_style(env.project, prompt_prefix="silver age comic art", lora_path=env.lora)
    set_lora(env.project, "style", None, env.lora, trigger_word="silver_v1")
    _write_enriched(env.project, "p1",
        characters=[_char("NOVA", lora=_lora("nova_v1"))],
        action="Nova sprints down the alley",
        shot_hint="wide tracking shot",
        mood="urgent",
        loc_description="rain-slicked neon alley",
    )
    build_prompts(env.project)
    text = (env.project.prompts_dir / "p1.txt").read_text().strip()

    # Order matters: style → style-trigger → char → action → shot → mood → setting.
    parts = [s.strip() for s in text.split(",")]
    # Find the index of each expected fragment (allowing for multi-comma fragments).
    full = text
    i_style = full.index("silver age comic art")
    i_style_trig = full.index("silver_v1")
    i_char = full.index("nova_v1")
    i_action = full.index("Nova sprints")
    i_shot = full.index("wide tracking")
    i_mood = full.index("urgent")
    i_setting = full.index("setting:")

    assert i_style < i_style_trig < i_char < i_action < i_shot < i_mood < i_setting
