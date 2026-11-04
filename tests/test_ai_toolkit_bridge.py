"""Tests for lazycomics.ai_toolkit_bridge.

All tests mock the subprocess calls: neither AI Toolkit nor Wan2GP is
required. Training-set generation, job-config construction, the training
run's bookkeeping and LoRA registration are exercised.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import yaml

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics.ai_toolkit_bridge import (  # noqa: E402
    _TRAINING_SHOTS,
    _build_job_config,
    _load_trainer_config,
    generate_training_set,
    train_character_lora,
)
from lazycomics.asset_registry import (  # noqa: E402
    get_character,
    register_character,
    register_style,
)
from lazycomics.project import create_project  # noqa: E402

DESC = "young man in his twenties, short messy brown hair, navy suit"
STYLE = "modern western comic art, bold inks"


# ---------------------------------------------------------------------------
# Test environment
# ---------------------------------------------------------------------------


class _Env:
    def __init__(self):
        from PIL import Image
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cbml = self.root / "story.cbml"
        cbml.write_text("## Test\naspect: 2:3\n\nPAGE preset:splash\n\nPANEL A\nloc: room\n> test\n")
        self.project = create_project("p1", cbml, base_dir=self.root)
        self.ref = self.root / "GERALD.png"
        Image.new("RGB", (64, 96), (120, 80, 40)).save(self.ref)
        register_character(self.project, "GERALD", description=DESC, reference_images=[self.ref])
        register_style(self.project, prompt_prefix=STYLE)

    @property
    def dataset(self) -> Path:
        return self.project.assets_dir / "characters" / "GERALD" / "lora_dataset"

    def lora_path(self) -> Path:
        job = "lazycomics_p1_GERALD"
        return self.project.assets_dir / "characters" / "GERALD" / "lora" / job / f"{job}.safetensors"

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


def _config(**ai_toolkit):
    return {
        "wan2gp": {
            "wgp_root": "/opt/wan2gp",
            "python_bin": "/opt/wan2gp/env/bin/python",
            "seed": 42,
        },
        "ai_toolkit": {
            "root": "/opt/ai-toolkit",
            "python_bin": "/opt/ai-toolkit/env/bin/python",
            **ai_toolkit,
        },
    }


def _fake_wgp(log):
    """``wgp.py --process`` stand-in: one PNG per task, named after it."""
    from PIL import Image

    def run(cmd, cwd, **kwargs):
        out_dir = Path(cmd[cmd.index("--output-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(cmd[cmd.index("--process") + 1]) as zf:
            tasks = [t["params"] for t in json.loads(zf.read("queue.json"))]
        log.extend(tasks)
        for t in tasks:
            w, h = (int(x) for x in t["resolution"].split("x"))
            Image.new("RGB", (w // 16, h // 16), (200, 0, 0)).save(out_dir / f"{t['output_filename']}.png")
        return mock.Mock(returncode=0)
    return run


def _fake_ai_toolkit(log, *, write_lora=True, returncode=0):
    """``run.py <config>`` stand-in: writes the LoRA where AI Toolkit would."""
    def run(cmd, cwd, **kwargs):
        job = yaml.safe_load(Path(cmd[2]).read_text(encoding="utf-8"))
        process = job["config"]["process"][0]
        log.append({"cmd": cmd, "cwd": cwd, "job": job})
        if write_lora:
            name = job["config"]["name"]
            out = Path(process["training_folder"]) / name / f"{name}.safetensors"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"lora")
        return mock.Mock(returncode=returncode)
    return run


def _fill_dataset(env, n):
    env.dataset.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (env.dataset / f"GERALD_{i:02d}.png").write_bytes(b"png")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_raises_when_paths_missing():
    try:
        _load_trainer_config({})
        assert False, "should have raised"
    except RuntimeError as e:
        assert "ai_toolkit.root" in str(e)


def test_config_defaults():
    cfg = _load_trainer_config(_config())
    assert cfg["arch"] == "flux2_klein_9b"
    assert cfg["name_or_path"] == "black-forest-labs/FLUX.2-klein-base-9B"
    assert cfg["steps"] == 2000 and cfg["rank"] == 16
    assert cfg["quantize"] is True and cfg["low_vram"] is True
    assert cfg["lora_weight"] == 1.0


def test_job_config_follows_ai_toolkit_schema():
    cfg = _load_trainer_config(_config(steps=300, rank=8, resolutions=[512]))
    job = _build_job_config("job1", Path("/data/set"), Path("/out"), "GERALD", cfg)
    # What AI Toolkit's run.py reads back.
    job = yaml.safe_load(yaml.safe_dump(job))
    assert job["job"] == "extension" and job["config"]["name"] == "job1"
    p = job["config"]["process"][0]
    assert p["type"] == "sd_trainer"
    assert p["training_folder"] == str(Path("/out"))
    assert p["trigger_word"] == "GERALD"
    assert p["network"] == {"type": "lora", "linear": 8, "linear_alpha": 8}
    assert p["datasets"][0]["folder_path"] == str(Path("/data/set"))
    assert p["datasets"][0]["resolution"] == [512]
    assert p["train"]["steps"] == 300
    assert p["train"]["cache_text_embeddings"] is True
    assert p["train"]["disable_sampling"] is True
    assert p["model"]["arch"] == "flux2_klein_9b"
    assert p["model"]["name_or_path"] == "black-forest-labs/FLUX.2-klein-base-9B"
    assert p["model"]["quantize"] is True and p["model"]["low_vram"] is True


# ---------------------------------------------------------------------------
# generate_training_set
# ---------------------------------------------------------------------------


@_with_env
def test_training_set_draws_every_shot_from_the_reference(env):
    log = []
    with mock.patch("lazycomics.wan2gp_bridge.subprocess.run", side_effect=_fake_wgp(log)):
        images = generate_training_set(env.project, "GERALD", config=_config())

    # Every shot, plus the reference itself.
    assert len(images) == len(_TRAINING_SHOTS) + 1
    assert (env.dataset / "GERALD_ref_1.png").is_file()
    assert (env.dataset / "GERALD_ref_1.txt").read_text().strip() == "GERALD"

    assert len(log) == len(_TRAINING_SHOTS)
    assert all(len(t["image_refs"]) == 1 for t in log)
    assert [t["seed"] for t in log] == [42 + n for n in range(1, len(_TRAINING_SHOTS) + 1)]
    assert all(DESC in t["prompt"] and "Modern western comic art" in t["prompt"] for t in log)
    assert log[0]["prompt"].startswith("Close-up portrait of Gerald, ")

    caption = (env.dataset / "GERALD_01.txt").read_text().strip()
    assert caption == "GERALD, close-up portrait, front view, neutral expression, plain light grey background"
    # The look is left to the trigger, so the description stays out of captions.
    assert not any(DESC in p.with_suffix(".txt").read_text() for p in images)


@_with_env
def test_training_set_keeps_picks_and_rejects(env):
    env.dataset.mkdir(parents=True)
    (env.dataset / "rejected").mkdir()
    (env.dataset / "GERALD_01.png").write_bytes(b"kept")
    (env.dataset / "rejected" / "GERALD_02.png").write_bytes(b"rejected")
    (env.dataset / "GERALD_03.txt").write_text("caption left behind")  # image gone: regenerate
    log = []
    with mock.patch("lazycomics.wan2gp_bridge.subprocess.run", side_effect=_fake_wgp(log)):
        images = generate_training_set(env.project, "GERALD", config=_config())

    names = {t["output_filename"] for t in log}
    assert "GERALD_01" not in names and "GERALD_02" not in names
    assert "GERALD_03" in names
    assert (env.dataset / "GERALD_01.png").read_bytes() == b"kept"
    assert env.dataset / "GERALD_02.png" not in images
    assert len(images) == len(_TRAINING_SHOTS)  # 20 shots + ref - 1 rejected


@_with_env
def test_training_set_force_starts_over(env):
    (env.dataset / "rejected").mkdir(parents=True)
    (env.dataset / "rejected" / "GERALD_02.png").write_bytes(b"rejected")
    log = []
    with mock.patch("lazycomics.wan2gp_bridge.subprocess.run", side_effect=_fake_wgp(log)):
        generate_training_set(env.project, "GERALD", force=True, config=_config())
    assert len(log) == len(_TRAINING_SHOTS)
    assert not (env.dataset / "rejected").exists()


@_with_env
def test_training_set_needs_a_reference_image(env):
    register_character(env.project, "INTERN", description="young office intern")
    try:
        generate_training_set(env.project, "INTERN", config=_config())
        assert False, "should have raised"
    except ValueError as e:
        assert "no reference image" in str(e)


# ---------------------------------------------------------------------------
# train_character_lora
# ---------------------------------------------------------------------------


@_with_env
def test_train_runs_ai_toolkit_and_registers_the_lora(env):
    _fill_dataset(env, 12)
    log = []
    with mock.patch("lazycomics.ai_toolkit_bridge.subprocess.run",
                    side_effect=_fake_ai_toolkit(log)):
        path = train_character_lora(env.project, "GERALD", config=_config(lora_weight=0.9))

    assert path == env.lora_path() and path.is_file()
    (call,) = log
    assert call["cmd"][:2] == [str(Path("/opt/ai-toolkit/env/bin/python").resolve()), "run.py"]
    assert call["cwd"] == str(Path("/opt/ai-toolkit").resolve())
    assert call["job"]["config"]["process"][0]["datasets"][0]["folder_path"] == str(env.dataset)

    lora = get_character(env.project, "GERALD").lora
    assert lora.path == path and lora.trigger_word == "GERALD" and lora.weight == 0.9


@_with_env
def test_train_skips_an_existing_lora_but_reattaches_it(env):
    _fill_dataset(env, 12)
    env.lora_path().parent.mkdir(parents=True)
    env.lora_path().write_bytes(b"trained earlier")
    # Re-registering (as every A/B run does) resets meta.json's LoRA.
    register_character(env.project, "GERALD", description=DESC, reference_images=[env.ref])

    with mock.patch("lazycomics.ai_toolkit_bridge.subprocess.run") as run:
        path = train_character_lora(env.project, "GERALD", config=_config())
    run.assert_not_called()
    assert get_character(env.project, "GERALD").lora.path == path == env.lora_path()


@_with_env
def test_train_force_deletes_the_old_job(env):
    _fill_dataset(env, 12)
    old_ckpt = env.lora_path().parent / "lazycomics_p1_GERALD_000000250.safetensors"
    old_ckpt.parent.mkdir(parents=True)
    old_ckpt.write_bytes(b"old")
    env.lora_path().write_bytes(b"old")

    seen = []

    def run(cmd, cwd, **kwargs):
        seen.append(old_ckpt.exists())  # AI Toolkit would resume from it
        return _fake_ai_toolkit([])(cmd, cwd)

    with mock.patch("lazycomics.ai_toolkit_bridge.subprocess.run", side_effect=run):
        train_character_lora(env.project, "GERALD", force=True, config=_config())
    assert seen == [False]
    assert env.lora_path().read_bytes() == b"lora"


@_with_env
def test_train_raises_when_ai_toolkit_fails(env):
    _fill_dataset(env, 12)
    with mock.patch("lazycomics.ai_toolkit_bridge.subprocess.run",
                    side_effect=_fake_ai_toolkit([], write_lora=False, returncode=1)):
        try:
            train_character_lora(env.project, "GERALD", config=_config())
            assert False, "should have raised"
        except RuntimeError as e:
            assert "exited with code 1" in str(e)
    assert get_character(env.project, "GERALD").lora is None


@_with_env
def test_train_raises_when_no_lora_is_written(env):
    _fill_dataset(env, 12)
    with mock.patch("lazycomics.ai_toolkit_bridge.subprocess.run",
                    side_effect=_fake_ai_toolkit([], write_lora=False)):
        try:
            train_character_lora(env.project, "GERALD", config=_config())
            assert False, "should have raised"
        except RuntimeError as e:
            assert "wrote no LoRA" in str(e)


@_with_env
def test_train_refuses_an_empty_dataset(env):
    with mock.patch("lazycomics.ai_toolkit_bridge.subprocess.run") as run:
        try:
            train_character_lora(env.project, "GERALD", config=_config())
            assert False, "should have raised"
        except FileNotFoundError as e:
            assert "generate_training_set" in str(e)
    run.assert_not_called()


@_with_env
def test_train_ignores_rejects_and_warns_on_a_small_set(env):
    _fill_dataset(env, 3)
    (env.dataset / "rejected").mkdir()
    for i in range(10):
        (env.dataset / "rejected" / f"GERALD_r{i}.png").write_bytes(b"png")
    buf = io.StringIO()
    with redirect_stdout(buf), mock.patch("lazycomics.ai_toolkit_bridge.subprocess.run",
                                          side_effect=_fake_ai_toolkit([])):
        train_character_lora(env.project, "GERALD", config=_config())
    assert "only 3 training image(s)" in buf.getvalue()
