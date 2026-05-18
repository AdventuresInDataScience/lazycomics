"""Tests for lazycomics.project."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics.project import create_project, load_project  # noqa: E402


class _TmpEnv:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cbml = self.root / "story.cbml"
        self.cbml.write_text(
            "## Test\naspect: 2:3\n\nPAGE preset:splash\n\nPANEL A\nloc: room\n> A test panel.\n"
        )

    def close(self):
        self.tmp.cleanup()


def _with_tmp(fn):
    def wrapper():
        env = _TmpEnv()
        try:
            fn(env)
        finally:
            env.close()
    wrapper.__name__ = fn.__name__
    return wrapper


# ---------------------------------------------------------------------------
# create_project
# ---------------------------------------------------------------------------


@_with_tmp
def test_create_project_makes_directory_tree(env):
    project = create_project("c1", env.cbml, base_dir=env.root)

    assert project.base_dir.is_dir()
    for sub in ("assets", "enriched", "prompts", "refs_prepared",
                "panels", "panels_text", "pages", "pages_upscaled", "output"):
        assert (project.base_dir / sub).is_dir(), f"missing subdir: {sub}"


@_with_tmp
def test_create_project_copies_cbml(env):
    project = create_project("c1", env.cbml, base_dir=env.root)
    assert project.cbml_path.is_file()
    assert project.cbml_path.read_text() == env.cbml.read_text()
    # Source is left untouched (copy, not move).
    assert env.cbml.is_file()


@_with_tmp
def test_create_project_writes_manifest(env):
    project = create_project("c1", env.cbml, base_dir=env.root)
    assert project.manifest_path.is_file()


@_with_tmp
def test_create_project_existing_directory_raises(env):
    create_project("c1", env.cbml, base_dir=env.root)
    try:
        create_project("c1", env.cbml, base_dir=env.root)
    except FileExistsError:
        pass
    else:
        raise AssertionError("Expected FileExistsError")


@_with_tmp
def test_create_project_missing_cbml_raises(env):
    try:
        create_project("c1", env.root / "nope.cbml", base_dir=env.root)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Expected FileNotFoundError")


@_with_tmp
def test_create_project_accepts_str_paths(env):
    project = create_project("c1", str(env.cbml), base_dir=str(env.root))
    assert project.base_dir.is_dir()


# ---------------------------------------------------------------------------
# load_project
# ---------------------------------------------------------------------------


@_with_tmp
def test_load_project_roundtrips(env):
    created = create_project("c1", env.cbml, base_dir=env.root)
    loaded = load_project(created.base_dir)
    assert loaded == created


@_with_tmp
def test_load_project_missing_manifest_raises(env):
    try:
        load_project(env.root)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Expected FileNotFoundError")
