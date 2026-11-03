"""Tests for lazycomics.upscaler.

The ONNX session is faked (nearest-neighbour 2x), so no model file or
onnxruntime is needed: the tests cover tiling, the source-hash record and
the skip rules.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics import upscaler  # noqa: E402
from lazycomics.project import create_project  # noqa: E402
from lazycomics.upscaler import SOURCE_KEY, upscale_panels  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSession:
    """Stands in for an onnxruntime session: nearest-neighbour ``scale`` x."""

    def __init__(self, scale: int = 2, dtype: str = "tensor(float)"):
        self.scale = scale
        self.calls = 0
        self.dtypes = set()
        self._input = mock.Mock(type=dtype)
        self._input.name = "input"

    def get_inputs(self):
        return [self._input]

    def run(self, _outputs, feeds):
        x = feeds["input"]
        self.calls += 1
        self.dtypes.add(x.dtype)
        return [x.repeat(self.scale, axis=2).repeat(self.scale, axis=3)]


class _Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cbml = self.root / "story.cbml"
        cbml.write_text(
            "## T\naspect: 2:3\n\nPAGE preset:splash\n\nPANEL A\nloc: r\n> t\n"
        )
        self.project = create_project("utest", cbml, base_dir=self.root)
        self.model = self.root / "model.onnx"
        self.model.write_bytes(b"onnx")
        self.config = {"upscaler": {"model_path": str(self.model)}}

    def write_panel(self, name: str = "p1", size=(40, 30), seed: int = 0) -> Path:
        rng = np.random.default_rng(seed)
        arr = rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)
        path = self.project.panels_dir / f"{name}.png"
        Image.fromarray(arr).save(path)
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


def _run(env, session=None, **kw):
    session = session or _FakeSession()
    with mock.patch.object(upscaler, "_open_session", return_value=session) as opened:
        results = upscale_panels(env.project, config=env.config, **kw)
    return results, session, opened


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@_with_env
def test_writes_each_panel_at_the_model_scale(env):
    env.write_panel("p1", (40, 30))
    env.write_panel("p2", (30, 40), seed=1)
    results, _, _ = _run(env)
    assert sorted(results) == ["p1", "p2"]
    assert Image.open(results["p1"]).size == (80, 60)
    assert Image.open(results["p2"]).size == (60, 80)
    assert results["p1"] == env.project.panels_upscaled_dir / "p1.png"


@_with_env
def test_tiles_stitch_into_the_whole_image(env):
    """A panel bigger than one tile comes out as if upscaled in one go."""
    src = env.write_panel("p1", (500, 420))
    results, session, _ = _run(env)
    assert session.calls == 4  # 2 x 2 tiles of 384
    want = np.asarray(Image.open(src)).repeat(2, axis=0).repeat(2, axis=1)
    assert np.array_equal(np.asarray(Image.open(results["p1"])), want)


@_with_env
def test_panel_smaller_than_the_padding(env):
    """Too small to reflect into the tile padding: edge-padded instead."""
    src = env.write_panel("p1", (12, 10))
    results, _, _ = _run(env)
    want = np.asarray(Image.open(src)).repeat(2, axis=0).repeat(2, axis=1)
    assert np.array_equal(np.asarray(Image.open(results["p1"])), want)


@_with_env
def test_float16_model_gets_float16_input(env):
    env.write_panel("p1")
    _, session, _ = _run(env, session=_FakeSession(dtype="tensor(float16)"))
    assert session.dtypes == {np.dtype(np.float16)}


@_with_env
def test_records_the_source_hash(env):
    src = env.write_panel("p1")
    results, _, _ = _run(env)
    with Image.open(results["p1"]) as im:
        assert im.info[SOURCE_KEY] == hashlib.sha1(src.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Skip rules
# ---------------------------------------------------------------------------


@_with_env
def test_skips_panels_already_upscaled_from_this_image(env):
    env.write_panel("p1")
    _run(env)
    results, _, opened = _run(env)
    opened.assert_not_called()  # nothing to do: the model isn't even loaded
    assert list(results) == ["p1"]


@_with_env
def test_redoes_a_panel_whose_source_changed(env):
    env.write_panel("p1", seed=0)
    _run(env)
    env.write_panel("p1", seed=1)  # regenerated
    _, session, _ = _run(env)
    assert session.calls == 1


@_with_env
def test_force_redoes_everything(env):
    env.write_panel("p1")
    _run(env)
    _, session, _ = _run(env, force=True)
    assert session.calls == 1


@_with_env
def test_only_the_named_panels(env):
    env.write_panel("p1")
    env.write_panel("p2", seed=1)
    results, _, _ = _run(env, panels=["p2", "missing"])
    assert list(results) == ["p2"]


@_with_env
def test_missing_model_raises(env):
    env.write_panel("p1")
    env.config["upscaler"]["model_path"] = str(env.root / "nope.onnx")
    try:
        upscale_panels(env.project, config=env.config)
        assert False, "should have raised"
    except FileNotFoundError as e:
        assert "upscaler.model_path" in str(e)


@_with_env
def test_unset_model_raises(env):
    env.write_panel("p1")
    try:
        upscale_panels(env.project, config={})
        assert False, "should have raised"
    except FileNotFoundError as e:
        assert "upscaler.model_path" in str(e)
