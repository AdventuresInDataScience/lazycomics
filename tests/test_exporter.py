"""Tests for lazycomics.exporter."""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics.exporter import export_cbz  # noqa: E402
from lazycomics.project import create_project  # noqa: E402


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


class _Env:
    def __init__(self, project_name="mycomic"):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cbml = self.root / "story.cbml"
        cbml.write_text(
            "## Test\naspect: 2:3\n\nPAGE preset:splash\n\nPANEL A\nloc: room\n> A test.\n"
        )
        self.project = create_project(project_name, cbml, base_dir=self.root)

    def make_page(self, page_num: int, w: int = 200, h: int = 300,
                  colour=(100, 100, 100)) -> Path:
        path = self.project.pages_dir / f"page_{page_num:03d}.png"
        Image.new("RGB", (w, h), colour).save(path)
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


# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------


@_with_env
def test_writes_cbz_at_default_path(env):
    env.make_page(1)
    out = export_cbz(env.project)
    assert out == env.project.output_dir / "mycomic.cbz"
    assert out.is_file()


@_with_env
def test_returns_path_to_cbz(env):
    env.make_page(1)
    out = export_cbz(env.project)
    assert isinstance(out, Path)
    assert out.suffix == ".cbz"


@_with_env
def test_default_filename_uses_project_name(env):
    env.make_page(1)
    out = export_cbz(env.project)
    assert out.name == "mycomic.cbz"


@_with_env
def test_custom_filename_used(env):
    env.make_page(1)
    out = export_cbz(env.project, output_filename="issue_1.cbz")
    assert out.name == "issue_1.cbz"
    assert out.is_file()


@_with_env
def test_extension_appended_when_missing(env):
    env.make_page(1)
    out = export_cbz(env.project, output_filename="issue_1")
    assert out.name == "issue_1.cbz"


@_with_env
def test_extension_appended_case_insensitive(env):
    # ".CBZ" already present (any case) → not double-appended.
    env.make_page(1)
    out = export_cbz(env.project, output_filename="issue_1.CBZ")
    assert out.name == "issue_1.CBZ"


# ---------------------------------------------------------------------------
# Archive contents
# ---------------------------------------------------------------------------


@_with_env
def test_archive_contains_all_pages(env):
    env.make_page(1)
    env.make_page(2)
    env.make_page(3)
    out = export_cbz(env.project)

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert set(names) == {"page_001.png", "page_002.png", "page_003.png"}


@_with_env
def test_archive_entries_in_sorted_order(env):
    # Create pages out of order; archive should still sort them.
    env.make_page(3)
    env.make_page(1)
    env.make_page(2)
    out = export_cbz(env.project)

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert names == ["page_001.png", "page_002.png", "page_003.png"]


@_with_env
def test_archive_uses_no_compression(env):
    env.make_page(1)
    out = export_cbz(env.project)

    with zipfile.ZipFile(out) as zf:
        info = zf.infolist()[0]
        assert info.compress_type == zipfile.ZIP_STORED


@_with_env
def test_archive_entries_use_bare_filenames(env):
    # No "pages/" prefix or absolute paths in archive names.
    env.make_page(1)
    out = export_cbz(env.project)

    with zipfile.ZipFile(out) as zf:
        for name in zf.namelist():
            assert "/" not in name
            assert "\\" not in name


@_with_env
def test_archived_png_round_trips(env):
    src = env.make_page(1, w=200, h=300, colour=(255, 0, 0))
    out = export_cbz(env.project)

    with zipfile.ZipFile(out) as zf:
        with zf.open("page_001.png") as f:
            assert f.read() == src.read_bytes()


# ---------------------------------------------------------------------------
# Skip / force
# ---------------------------------------------------------------------------


@_with_env
def test_skip_when_archive_exists(env):
    env.make_page(1)
    export_cbz(env.project)

    out_path = env.project.output_dir / "mycomic.cbz"
    out_path.write_bytes(b"USER_OVERRIDE")

    returned = export_cbz(env.project)  # default force=False
    assert returned == out_path
    assert out_path.read_bytes() == b"USER_OVERRIDE"


@_with_env
def test_force_overwrites_existing(env):
    env.make_page(1)
    export_cbz(env.project)

    out_path = env.project.output_dir / "mycomic.cbz"
    out_path.write_bytes(b"USER_OVERRIDE")

    export_cbz(env.project, force=True)
    assert out_path.read_bytes() != b"USER_OVERRIDE"
    # Re-opens as a valid ZIP
    with zipfile.ZipFile(out_path) as zf:
        assert zf.namelist() == ["page_001.png"]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@_with_env
def test_raises_when_no_pages_to_export(env):
    # No pages created.
    try:
        export_cbz(env.project)
    except FileNotFoundError as e:
        assert "page_*.png" in str(e)
    else:
        raise AssertionError("Expected FileNotFoundError")


@_with_env
def test_only_page_pattern_files_are_archived(env):
    # Stray non-matching file in pages_dir should not enter the archive.
    env.make_page(1)
    (env.project.pages_dir / "notes.txt").write_text("draft notes")
    (env.project.pages_dir / "thumb_001.png").write_bytes(b"not a page")

    out = export_cbz(env.project)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert names == ["page_001.png"]
