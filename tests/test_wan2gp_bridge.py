"""Tests for lazycomics.wan2gp_bridge.

All tests mock the subprocess call — Wan2GP is not required. The bridge
logic (config resolution, resolution snapping, task construction, queue
building, output collection) is fully exercised.
"""

from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics.project import create_project  # noqa: E402
from lazycomics.wan2gp_bridge import (  # noqa: E402
    _build_inpaint_task,
    _build_inpaint_prompt,
    _build_panel_task,
    _build_queue_zip,
    _build_wangp_task,
    _collect_loras,
    _collect_outputs,
    _collect_refs,
    _compute_resolution,
    _ensure_lora,
    _generate_mask,
    _get_character_region,
    _load_bridge_config,
    _read_prompt,
    generate_panels,
)


# ---------------------------------------------------------------------------
# Test environment
# ---------------------------------------------------------------------------


class _Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cbml = self.root / "story.cbml"
        cbml.write_text(
            "## Test\naspect: 2:3\n\nPAGE preset:splash\n\n"
            "PANEL A\nloc: room\n> test\n"
        )
        self.project = create_project("p1", cbml, base_dir=self.root)

    def write_enriched(self, panel_id: str, **fields):
        data = {
            "panel_id": panel_id,
            "page_index": 0,
            "panel_index": 0,
            "aspect_ratio": 2 / 3,
            "shot_hint": "",
            "characters": [],
            "primary_character": None,
            "char_generation_strategy": "single",
            "inpaint_order": [],
        }
        data.update(fields)
        (self.project.enriched_dir / f"{panel_id}.json").write_text(
            json.dumps(data)
        )

    def write_prompt(self, panel_id: str, text: str, negative: bool = False):
        suffix = ".neg.txt" if negative else ".txt"
        (self.project.prompts_dir / f"{panel_id}{suffix}").write_text(text)

    def write_ref(self, panel_id: str, suffix: str = "_ref.png"):
        path = self.project.refs_prepared_dir / f"{panel_id}{suffix}"
        path.write_bytes(b"fakepng")
        return path

    def write_panel(self, panel_id: str):
        """Write a fake existing panel to test skip logic."""
        path = self.project.panels_dir / f"{panel_id}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"existing")
        return path

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


def _base_config():
    return {
        "wan2gp": {
            "wgp_root": "/opt/pinokio/api/wan2gp.git/Wan2GP",
            "python_bin": "/opt/pinokio/api/wan2gp.git/env/bin/python",
            "architecture": "flux2_klein_9b",
            "default_steps": 4,
            "guidance_scale": 5,
            "video_prompt_type": "KI",
            "base_resolution": 1024,
            "seed": None,
            "cli_args": [],
        }
    }


# ---------------------------------------------------------------------------
# _load_bridge_config
# ---------------------------------------------------------------------------


def test_config_raises_when_paths_missing():
    try:
        _load_bridge_config({})
        assert False, "should have raised"
    except RuntimeError as e:
        assert "wgp_root" in str(e)


def test_config_loads_defaults():
    cfg = _load_bridge_config(_base_config())
    assert cfg["architecture"] == "flux2_klein_9b"
    assert cfg["default_steps"] == 4
    assert cfg["guidance_scale"] == 5
    assert cfg["base_resolution"] == 1024
    assert cfg["seed"] is None
    assert cfg["cli_args"] == []


def test_config_overrides():
    raw = _base_config()
    raw["wan2gp"]["default_steps"] = 12
    raw["wan2gp"]["seed"] = 42
    cfg = _load_bridge_config(raw)
    assert cfg["default_steps"] == 12
    assert cfg["seed"] == 42


# ---------------------------------------------------------------------------
# _compute_resolution
# ---------------------------------------------------------------------------


def test_resolution_portrait():
    # 2:3 aspect = 0.667, portrait. Height=1024, width=1024*0.667=683→snapped to 704
    res = _compute_resolution(2 / 3, 1024)
    w, h = res.split("x")
    assert int(h) == 1024
    assert int(w) % 64 == 0
    assert int(w) > 0


def test_resolution_landscape():
    # 3:2 = 1.5, landscape. Width=1024, height=1024/1.5=683→snapped
    res = _compute_resolution(3 / 2, 1024)
    w, h = res.split("x")
    assert int(w) == 1024
    assert int(h) % 64 == 0


def test_resolution_square():
    res = _compute_resolution(1.0, 1024)
    assert res == "1024x1024"


def test_resolution_extreme_portrait():
    res = _compute_resolution(1 / 3, 1024)
    w, h = res.split("x")
    assert int(h) == 1024
    assert int(w) % 64 == 0
    assert int(w) < int(h)


# ---------------------------------------------------------------------------
# _read_prompt / _collect_refs
# ---------------------------------------------------------------------------


@_with_env
def test_read_prompt_positive(env):
    env.write_prompt("p1", "a cool scene")
    assert _read_prompt(env.project.prompts_dir, "p1") == "a cool scene"


@_with_env
def test_read_prompt_negative(env):
    env.write_prompt("p1", "blurry", negative=True)
    assert _read_prompt(env.project.prompts_dir, "p1", negative=True) == "blurry"


@_with_env
def test_read_prompt_missing_returns_empty(env):
    assert _read_prompt(env.project.prompts_dir, "p1") == ""


@_with_env
def test_collect_refs_primary_only(env):
    env.write_ref("p1", "_ref.png")
    refs = _collect_refs(env.project.refs_prepared_dir, "p1")
    assert len(refs) == 1
    assert refs[0].name == "p1_ref.png"


@_with_env
def test_collect_refs_primary_plus_supporting(env):
    env.write_ref("p1", "_ref.png")
    env.write_ref("p1", "_ref_1.png")
    env.write_ref("p1", "_ref_2.png")
    refs = _collect_refs(env.project.refs_prepared_dir, "p1")
    assert [r.name for r in refs] == ["p1_ref.png", "p1_ref_1.png", "p1_ref_2.png"]


@_with_env
def test_collect_refs_empty_when_no_files(env):
    assert _collect_refs(env.project.refs_prepared_dir, "p1") == []


# ---------------------------------------------------------------------------
# _build_wangp_task
# ---------------------------------------------------------------------------


def test_task_has_required_fields():
    cfg = _load_bridge_config(_base_config())
    task = _build_wangp_task(
        panel_data={"panel_id": "p1"},
        prompt="a scene",
        negative_prompt="blurry",
        refs=[Path("/img/ref.png")],
        resolution="1024x1536",
        bridge_cfg=cfg,
    )
    assert task["model_type"] == "flux2_klein_9b"
    assert task["image_mode"] == 1
    assert task["prompt"] == "a scene"
    assert task["negative_prompt"] == "blurry"
    assert task["resolution"] == "1024x1536"
    assert task["num_inference_steps"] == 4
    assert task["guidance_scale"] == 5
    assert task["seed"] == -1  # None seed → -1 (random)
    assert task["image_start"] == str(Path("/img/ref.png"))


def test_task_with_explicit_seed():
    raw = _base_config()
    raw["wan2gp"]["seed"] = 42
    cfg = _load_bridge_config(raw)
    task = _build_wangp_task({}, "x", "", [], "1024x1024", cfg)
    assert task["seed"] == 42


def test_task_supporting_refs():
    cfg = _load_bridge_config(_base_config())
    task = _build_wangp_task(
        {}, "x", "", [Path("/a.png"), Path("/b.png"), Path("/c.png")],
        "1024x1024", cfg,
    )
    assert task["image_start"] == str(Path("/a.png"))
    assert task["image_refs"] == [str(Path("/b.png")), str(Path("/c.png"))]


def test_task_no_refs():
    cfg = _load_bridge_config(_base_config())
    task = _build_wangp_task({}, "x", "", [], "1024x1024", cfg)
    assert "image_start" not in task
    assert "image_refs" not in task


def test_task_empty_negative_prompt_still_present():
    """Wan2GP always expects negative_prompt as a flat string field."""
    cfg = _load_bridge_config(_base_config())
    task = _build_wangp_task({}, "x", "", [], "1024x1024", cfg)
    assert task["negative_prompt"] == ""


# ---------------------------------------------------------------------------
# _build_queue_zip
# ---------------------------------------------------------------------------


def test_queue_zip_structure():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # Create a fake ref image
        ref = tmp / "ref.png"
        ref.write_bytes(b"fakepng")

        tasks = [
            {
                "model": {"architecture": "flux_test"},
                "prompt": "hello",
                "resolution": "1024x1024",
                "image_start": str(ref),
            }
        ]

        queue_path = tmp / "queue.zip"
        _build_queue_zip(tasks, queue_path)

        assert queue_path.is_file()
        with zipfile.ZipFile(queue_path) as zf:
            names = zf.namelist()
            assert "queue.json" in names
            assert any("image_start" in n for n in names)

            settings = json.loads(zf.read("queue.json"))
            assert len(settings) == 1
            # image_start should be rewritten to zip-internal filename
            assert settings[0]["params"]["image_start"].startswith("task0_image_start_")


def test_queue_zip_multiple_tasks():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        ref1 = tmp / "r1.png"
        ref1.write_bytes(b"fake1")
        ref2 = tmp / "r2.png"
        ref2.write_bytes(b"fake2")

        tasks = [
            {"prompt": "one", "image_start": str(ref1)},
            {"prompt": "two", "image_start": str(ref2)},
        ]

        queue_path = tmp / "queue.zip"
        _build_queue_zip(tasks, queue_path)

        with zipfile.ZipFile(queue_path) as zf:
            settings = json.loads(zf.read("queue.json"))
            assert len(settings) == 2
            assert settings[0]["params"]["image_start"].startswith("task0_")
            assert settings[1]["params"]["image_start"].startswith("task1_")


# ---------------------------------------------------------------------------
# _collect_outputs
# ---------------------------------------------------------------------------


@_with_env
def test_collect_outputs_copies_and_renames(env):
    env.project.panels_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as out_dir:
        out_dir = Path(out_dir)
        # Simulate Wan2GP output — two numbered images
        (out_dir / "00001.png").write_bytes(b"img1")
        (out_dir / "00002.png").write_bytes(b"img2")

        results = _collect_outputs(out_dir, ["p1", "p2"], env.project)

        assert "p1" in results
        assert "p2" in results
        assert (env.project.panels_dir / "p1.png").read_bytes() == b"img1"
        assert (env.project.panels_dir / "p2.png").read_bytes() == b"img2"


@_with_env
def test_collect_outputs_handles_count_mismatch(env):
    env.project.panels_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as out_dir:
        out_dir = Path(out_dir)
        # Only one image for two panel IDs
        (out_dir / "00001.png").write_bytes(b"img1")

        results = _collect_outputs(out_dir, ["p1", "p2"], env.project)
        # Should pair what it can
        assert "p1" in results
        assert "p2" not in results


# ---------------------------------------------------------------------------
# generate_panels — integration (mock subprocess)
# ---------------------------------------------------------------------------


@_with_env
def test_generate_panels_skips_existing(env):
    env.write_enriched("p1")
    env.write_prompt("p1", "test prompt")
    env.write_panel("p1")  # already exists

    # Should return the existing path without calling Wan2GP
    results = generate_panels(env.project, config=_base_config())
    assert "p1" in results
    assert results["p1"].name == "p1.png"


@_with_env
def test_generate_panels_force_regenerates(env):
    env.write_enriched("p1")
    env.write_prompt("p1", "test prompt")
    env.write_ref("p1")
    env.write_panel("p1")

    def fake_run(cmd, cwd, **kwargs):
        # Simulate Wan2GP writing one output image
        out_dir = cmd[cmd.index("--output-dir") + 1]
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "00001.png").write_bytes(b"regenerated")
        return mock.Mock(returncode=0)

    with mock.patch("lazycomics.wan2gp_bridge.subprocess.run", side_effect=fake_run):
        results = generate_panels(env.project, force=True, config=_base_config())

    assert "p1" in results
    assert (env.project.panels_dir / "p1.png").read_bytes() == b"regenerated"


@_with_env
def test_generate_panels_subprocess_failure_raises(env):
    env.write_enriched("p1")
    env.write_prompt("p1", "test prompt")

    with mock.patch(
        "lazycomics.wan2gp_bridge.subprocess.run",
        return_value=mock.Mock(returncode=1),
    ):
        try:
            generate_panels(env.project, force=True, config=_base_config())
            assert False, "should have raised"
        except RuntimeError as e:
            # stderr is no longer captured (it streams to the user's
            # terminal live), so the exception just references the exit code.
            assert "exited with code 1" in str(e)


@_with_env
def test_generate_panels_no_enriched_returns_empty(env):
    results = generate_panels(env.project, config=_base_config())
    assert results == {}


# ---------------------------------------------------------------------------
# LoRA collection and wiring
# ---------------------------------------------------------------------------


def test_task_with_loras():
    cfg = _load_bridge_config(_base_config())
    task = _build_wangp_task(
        {}, "x", "", [], "1024x1024", cfg,
        activated_loras=["char.safetensors"],
        loras_multipliers="0.7",
    )
    assert task["activated_loras"] == ["char.safetensors"]
    assert task["loras_multipliers"] == "0.7"


def test_task_without_loras():
    cfg = _load_bridge_config(_base_config())
    task = _build_wangp_task({}, "x", "", [], "1024x1024", cfg)
    assert task["activated_loras"] == []
    assert task["loras_multipliers"] == ""


def test_collect_loras_single_char():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        loras_dir = tmp / "loras"
        loras_dir.mkdir()
        lora_src = tmp / "nova.safetensors"
        lora_src.write_bytes(b"fakeweights")

        panel = {"characters": [{
            "identifier": "NOVA",
            "lora": {"path": str(lora_src), "weight": 0.8, "trigger_word": "nova_v1"},
        }]}
        cfg = {"loras_dir": loras_dir}

        names, mults, triggers = _collect_loras(panel, cfg)
        assert names == ["nova.safetensors"]
        assert mults == "0.8"
        assert triggers == ["nova_v1"]
        assert (loras_dir / "nova.safetensors").is_file()


def test_collect_loras_multi_char():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        loras_dir = tmp / "loras"
        loras_dir.mkdir()
        l1 = tmp / "a.safetensors"; l1.write_bytes(b"l1")
        l2 = tmp / "b.safetensors"; l2.write_bytes(b"l2")

        panel = {"characters": [
            {"identifier": "A", "lora": {"path": str(l1), "weight": 0.8, "trigger_word": "tw_a"}},
            {"identifier": "B", "lora": {"path": str(l2), "weight": 0.6, "trigger_word": None}},
        ]}
        cfg = {"loras_dir": loras_dir}

        names, mults, triggers = _collect_loras(panel, cfg)
        assert names == ["a.safetensors", "b.safetensors"]
        assert mults == "0.8,0.6"
        assert triggers == ["tw_a"]


def test_collect_loras_no_loras():
    cfg = {"loras_dir": Path("/tmp")}
    names, mults, triggers = _collect_loras({"characters": []}, cfg)
    assert names == [] and mults == "" and triggers == []


def test_collect_loras_skips_null_lora():
    cfg = {"loras_dir": Path("/tmp")}
    panel = {"characters": [
        {"identifier": "X", "lora": None},
    ]}
    names, mults, triggers = _collect_loras(panel, cfg)
    assert names == []


def test_ensure_lora_copies_file():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "test.safetensors"
        src.write_bytes(b"weights")
        loras_dir = tmp / "loras"

        dst = _ensure_lora(src, loras_dir)
        assert dst == loras_dir / "test.safetensors"
        assert dst.read_bytes() == b"weights"


def test_ensure_lora_skips_existing_same_size():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "test.safetensors"
        src.write_bytes(b"weights")
        loras_dir = tmp / "loras"
        loras_dir.mkdir()
        existing = loras_dir / "test.safetensors"
        existing.write_bytes(b"weights")  # same size

        dst = _ensure_lora(src, loras_dir)
        assert dst == existing


# ---------------------------------------------------------------------------
# Inpaint task builder
# ---------------------------------------------------------------------------


def test_inpaint_task_fields():
    cfg = _load_bridge_config(_base_config())
    task = _build_inpaint_task(
        source_image=Path("/img/panel.png"),
        mask_image=Path("/img/mask.png"),
        prompt="a warrior", negative_prompt="blurry",
        resolution="704x1024", bridge_cfg=cfg,
    )
    assert task["image_mode"] == 2
    assert task["video_prompt_type"] == "VAG"
    assert task["denoising_strength"] == 0.65
    assert task["masking_strength"] == 0.3
    assert Path(task["image_guide"]) == Path("/img/panel.png")
    assert Path(task["image_mask"]) == Path("/img/mask.png")


def test_inpaint_task_with_loras():
    cfg = _load_bridge_config(_base_config())
    task = _build_inpaint_task(
        Path("/p.png"), Path("/m.png"), "x", "", "1024x1024", cfg,
        activated_loras=["hero.safetensors"], loras_multipliers="0.8",
    )
    assert task["activated_loras"] == ["hero.safetensors"]


# ---------------------------------------------------------------------------
# Character region + mask generation
# ---------------------------------------------------------------------------


def test_character_region_from_bubble():
    bubble = {"character": "REX", "x_frac": 0.5, "y_frac": 0.7,
              "width_frac": 0.4, "height_frac": 0.2}
    region = _get_character_region("REX", {"REX": bubble}, 3, 1)
    assert region[3] == 1.0  # full height
    assert region[0] >= 0.0


def test_character_region_fallback():
    region = _get_character_region("GHOST", {}, 3, 2)
    assert abs(region[0] - 2 / 3) < 0.01
    assert abs(region[2] - 1 / 3) < 0.01


@_with_env
def test_generate_mask_creates_correct_image(env):
    from PIL import Image
    env.project.panels_dir.mkdir(parents=True, exist_ok=True)
    mask_path = _generate_mask(704, 1024, (0.5, 0.0, 0.5, 1.0),
                               env.project, "p1", 1)
    assert mask_path.is_file()
    mask = Image.open(mask_path)
    assert mask.size == (704, 1024)
    assert mask.getpixel((10, 512)) == 0       # left = black (preserve)
    assert mask.getpixel((600, 512)) == 255    # right = white (repaint)


# ---------------------------------------------------------------------------
# Inpaint prompt
# ---------------------------------------------------------------------------


def test_inpaint_prompt_full():
    p = _build_inpaint_prompt(
        {"identifier": "REX", "visual_description": "a grizzled warrior"},
        {"mood": "dark", "action": "standing guard"},
    )
    assert "grizzled warrior" in p
    assert "dark mood" in p
    assert "standing guard" in p


def test_inpaint_prompt_fallback():
    p = _build_inpaint_prompt({"identifier": "REX"}, {})
    assert "REX" in p


# ---------------------------------------------------------------------------
# Multi-inpaint integration
# ---------------------------------------------------------------------------


@_with_env
def test_multi_inpaint_runs_base_plus_inpaint(env):
    env.write_enriched(
        "p1",
        char_generation_strategy="multi_inpaint",
        primary_character="NOVA",
        inpaint_order=["NOVA", "REX"],
        characters=[
            {"identifier": "NOVA", "visual_description": "a pilot",
             "reference_images": [], "lora": None},
            {"identifier": "REX", "visual_description": "a warrior",
             "reference_images": [], "lora": None},
        ],
        bubble_layout=[
            {"character": "NOVA", "x_frac": 0.0, "y_frac": 0.7,
             "width_frac": 0.5, "height_frac": 0.2},
            {"character": "REX", "x_frac": 0.5, "y_frac": 0.7,
             "width_frac": 0.5, "height_frac": 0.2},
        ],
        mood="tense",
    )
    env.write_prompt("p1", "two characters in a standoff")

    call_count = [0]

    def fake_run(cmd, cwd, **kwargs):
        call_count[0] += 1
        od = cmd[cmd.index("--output-dir") + 1]
        Path(od).mkdir(parents=True, exist_ok=True)
        (Path(od) / "00001.png").write_bytes(
            b"pass_" + str(call_count[0]).encode()
        )
        return mock.Mock(returncode=0)

    with mock.patch("lazycomics.wan2gp_bridge.subprocess.run",
                    side_effect=fake_run):
        results = generate_panels(env.project, force=True,
                                  config=_base_config())

    assert "p1" in results
    assert call_count[0] == 2  # base + 1 inpaint
    assert (env.project.panels_dir / "p1.png").read_bytes() == b"pass_2"


@_with_env
def test_single_and_multi_panels_in_same_run(env):
    # Single panel
    env.write_enriched("s1", char_generation_strategy="single",
                       characters=[])
    env.write_prompt("s1", "a landscape")

    # Multi panel
    env.write_enriched(
        "m1",
        char_generation_strategy="multi_inpaint",
        primary_character="A",
        inpaint_order=["A", "B"],
        characters=[
            {"identifier": "A", "visual_description": "char A",
             "reference_images": [], "lora": None},
            {"identifier": "B", "visual_description": "char B",
             "reference_images": [], "lora": None},
        ],
        bubble_layout=[],
    )
    env.write_prompt("m1", "two chars")

    calls = []

    def fake_run(cmd, cwd, **kwargs):
        calls.append(cmd)
        od = cmd[cmd.index("--output-dir") + 1]
        Path(od).mkdir(parents=True, exist_ok=True)
        (Path(od) / "00001.png").write_bytes(b"img")
        return mock.Mock(returncode=0)

    with mock.patch("lazycomics.wan2gp_bridge.subprocess.run",
                    side_effect=fake_run):
        results = generate_panels(env.project, force=True,
                                  config=_base_config())

    assert "s1" in results
    assert "m1" in results
    # 1 batch call for single + 2 sequential for multi = 3
    assert len(calls) == 3
