"""Tests for lazycomics.upscaler.

All tests mock the subprocess call — Real-ESRGAN binary not required.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest import mock

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics.project import create_project  # noqa: E402
from lazycomics.upscaler import (  # noqa: E402
    _load_upscaler_config,
    _run_realesrgan,
    upscale_pages,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cbml = self.root / "story.cbml"
        cbml.write_text(
            "## T\naspect: 2:3\n\nPAGE preset:splash\n\nPANEL A\nloc: r\n> t\n"
        )
        self.project = create_project("utest", cbml, base_dir=self.root)

    def write_page(self, name: str = "page_1.png"):
        self.project.pages_dir.mkdir(parents=True, exist_ok=True)
        p = self.project.pages_dir / name
        p.write_bytes(b"fakepage")
        return p

    def write_upscaled(self, name: str = "page_1.png"):
        self.project.pages_upscaled_dir.mkdir(parents=True, exist_ok=True)
        p = self.project.pages_upscaled_dir / name
        p.write_bytes(b"upscaled")
        return p

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
        "upscaler": {
            "executable": "realesrgan-ncnn-vulkan",
            "model": "realesrgan-x4plus-anime",
            "scale": 4,
        }
    }


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_defaults():
    cfg = _load_upscaler_config({})
    assert cfg["model"] == "realesrgan-x4plus-anime"
    assert cfg["scale"] == 4


def test_config_overrides():
    cfg = _load_upscaler_config({
        "upscaler": {"model": "realesrgan-x4plus", "scale": 2}
    })
    assert cfg["model"] == "realesrgan-x4plus"
    assert cfg["scale"] == 2


# ---------------------------------------------------------------------------
# upscale_pages
# ---------------------------------------------------------------------------


@_with_env
def test_upscale_no_pages_returns_empty(env):
    results = upscale_pages(env.project, config=_base_config())
    assert results == []


@_with_env
def test_upscale_skips_when_all_exist(env):
    env.write_page("page_1.png")
    env.write_upscaled("page_1.png")

    # Should not call subprocess at all
    with mock.patch("lazycomics.upscaler.subprocess.run") as mock_run:
        results = upscale_pages(env.project, config=_base_config())
        mock_run.assert_not_called()

    assert len(results) == 1
    assert results[0].name == "page_1.png"


@_with_env
def test_upscale_force_regenerates(env):
    env.write_page("page_1.png")
    env.write_upscaled("page_1.png")

    def fake_run(cmd, capture_output, text):
        # Simulate writing upscaled output
        out_dir = cmd[cmd.index("-o") + 1]
        (Path(out_dir) / "page_1.png").write_bytes(b"re-upscaled")
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch("lazycomics.upscaler.subprocess.run", side_effect=fake_run):
        results = upscale_pages(env.project, force=True, config=_base_config())

    assert len(results) == 1
    assert results[0].read_bytes() == b"re-upscaled"


@_with_env
def test_upscale_passes_correct_args(env):
    env.write_page("page_1.png")

    captured_cmd = []

    def fake_run(cmd, capture_output, text):
        captured_cmd.extend(cmd)
        out_dir = cmd[cmd.index("-o") + 1]
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "page_1.png").write_bytes(b"upscaled")
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch("lazycomics.upscaler.subprocess.run", side_effect=fake_run):
        upscale_pages(env.project, force=True, config=_base_config())

    assert "-i" in captured_cmd
    assert "-o" in captured_cmd
    assert "-n" in captured_cmd
    n_idx = captured_cmd.index("-n")
    assert captured_cmd[n_idx + 1] == "realesrgan-x4plus-anime"
    s_idx = captured_cmd.index("-s")
    assert captured_cmd[s_idx + 1] == "4"


@_with_env
def test_upscale_multiple_pages(env):
    env.write_page("page_1.png")
    env.write_page("page_2.png")
    env.write_page("page_3.png")

    def fake_run(cmd, capture_output, text):
        out_dir = cmd[cmd.index("-o") + 1]
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        for i in range(1, 4):
            (Path(out_dir) / f"page_{i}.png").write_bytes(f"up_{i}".encode())
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch("lazycomics.upscaler.subprocess.run", side_effect=fake_run):
        results = upscale_pages(env.project, force=True, config=_base_config())

    assert len(results) == 3


@_with_env
def test_upscale_failure_raises(env):
    env.write_page("page_1.png")

    with mock.patch(
        "lazycomics.upscaler.subprocess.run",
        return_value=mock.Mock(returncode=1, stdout="", stderr="Vulkan error"),
    ):
        try:
            upscale_pages(env.project, force=True, config=_base_config())
            assert False, "should have raised"
        except RuntimeError as e:
            assert "Vulkan error" in str(e)
