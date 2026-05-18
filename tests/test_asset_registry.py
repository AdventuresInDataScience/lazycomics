"""Tests for lazycomics.asset_registry."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics.asset_registry import (  # noqa: E402
    get_character,
    get_location,
    get_style,
    register_character,
    register_location,
    register_style,
    set_lora,
)
from lazycomics.models import CharacterRef, LocationAsset, LoRAConfig, StyleAsset  # noqa: E402
from lazycomics.project import create_project  # noqa: E402


# ---------------------------------------------------------------------------
# Test environment: tmp project + a few "image" files (any bytes will do).
# ---------------------------------------------------------------------------


class _Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        # Minimal CBML so create_project succeeds.
        cbml = self.root / "story.cbml"
        cbml.write_text("## Test\naspect: 2:3\n\nPAGE preset:splash\n\nPANEL A\nloc: room\n> A test.\n")
        self.project = create_project("p1", cbml, base_dir=self.root)

        # A few stand-in image files.
        self.img_dir = self.root / "src_images"
        self.img_dir.mkdir()
        for name in ("nova1.png", "nova2.png", "style1.png", "wh1.png"):
            (self.img_dir / name).write_bytes(b"fake-png-bytes")

        # A stand-in LoRA file.
        self.lora = self.root / "nova.safetensors"
        self.lora.write_bytes(b"fake-lora-bytes")

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
# register_character
# ---------------------------------------------------------------------------


@_with_env
def test_register_character_creates_layout(env):
    register_character(
        env.project, "NOVA",
        description="blue hair",
        reference_images=[env.img_dir / "nova1.png", env.img_dir / "nova2.png"],
    )
    nova_dir = env.project.assets_dir / "characters" / "NOVA"
    assert nova_dir.is_dir()
    assert (nova_dir / "meta.json").is_file()
    assert (nova_dir / "references").is_dir()
    assert (nova_dir / "references" / "nova1.png").is_file()
    assert (nova_dir / "references" / "nova2.png").is_file()


@_with_env
def test_register_character_meta_contents(env):
    register_character(
        env.project, "NOVA",
        description="blue hair, leather jacket",
        reference_images=[env.img_dir / "nova1.png"],
        lora_path=env.lora,
        lora_weight=0.65,
    )
    meta = json.loads((env.project.assets_dir / "characters" / "NOVA" / "meta.json").read_text())
    assert meta["identifier"] == "NOVA"
    assert meta["description"] == "blue hair, leather jacket"
    assert meta["lora_path"] == str(env.lora)
    assert meta["lora_weight"] == 0.65


@_with_env
def test_register_character_no_lora_stores_nulls(env):
    register_character(env.project, "NOVA", description="x")
    meta = json.loads((env.project.assets_dir / "characters" / "NOVA" / "meta.json").read_text())
    assert meta["lora_path"] is None
    assert meta["lora_weight"] is None


@_with_env
def test_register_character_missing_source_image_raises(env):
    try:
        register_character(
            env.project, "NOVA",
            reference_images=[env.img_dir / "does_not_exist.png"],
        )
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Expected FileNotFoundError")


@_with_env
def test_register_character_missing_image_leaves_dir_clean(env):
    """Half-create guard: a missing source image shouldn't leave a stub directory."""
    try:
        register_character(
            env.project, "NOVA",
            reference_images=[env.img_dir / "nova1.png", env.img_dir / "missing.png"],
        )
    except FileNotFoundError:
        pass
    # The NOVA dir should not have been created with one ref and no meta.
    nova_dir = env.project.assets_dir / "characters" / "NOVA"
    if nova_dir.exists():
        # If anything was created, it must include the meta + both refs (i.e. all-or-nothing).
        # Since the second source is missing, nothing should be created.
        raise AssertionError(f"Stub directory left behind: {list(nova_dir.iterdir())}")


@_with_env
def test_register_character_idempotent_replaces_refs(env):
    register_character(env.project, "NOVA", reference_images=[env.img_dir / "nova1.png"])
    # Re-register with different refs.
    register_character(env.project, "NOVA", reference_images=[env.img_dir / "nova2.png"])

    refs_dir = env.project.assets_dir / "characters" / "NOVA" / "references"
    files = {p.name for p in refs_dir.iterdir()}
    assert files == {"nova2.png"}, files


# ---------------------------------------------------------------------------
# register_style / register_location
# ---------------------------------------------------------------------------


@_with_env
def test_register_style_creates_assets_style(env):
    register_style(
        env.project,
        preset="silver_age",
        prompt_prefix="bold inks,",
        reference_images=[env.img_dir / "style1.png"],
    )
    style_dir = env.project.assets_dir / "style"
    assert style_dir.is_dir()
    assert (style_dir / "meta.json").is_file()
    assert (style_dir / "references" / "style1.png").is_file()

    meta = json.loads((style_dir / "meta.json").read_text())
    assert meta["preset"] == "silver_age"
    assert meta["prompt_prefix"] == "bold inks,"


@_with_env
def test_register_location(env):
    register_location(
        env.project, "warehouse_night",
        description="dark industrial warehouse",
        reference_images=[env.img_dir / "wh1.png"],
    )
    loc_dir = env.project.assets_dir / "locations" / "warehouse_night"
    assert loc_dir.is_dir()
    meta = json.loads((loc_dir / "meta.json").read_text())
    assert meta["identifier"] == "warehouse_night"
    assert meta["description"] == "dark industrial warehouse"


# ---------------------------------------------------------------------------
# set_lora
# ---------------------------------------------------------------------------


@_with_env
def test_set_lora_for_character(env):
    register_character(env.project, "NOVA", description="x")
    set_lora(env.project, "character", "NOVA", env.lora, weight=0.6)

    meta = json.loads((env.project.assets_dir / "characters" / "NOVA" / "meta.json").read_text())
    assert meta["lora_path"] == str(env.lora)
    assert meta["lora_weight"] == 0.6
    # Unrelated fields preserved.
    assert meta["description"] == "x"


@_with_env
def test_set_lora_for_style_uses_none_identifier(env):
    register_style(env.project, preset="silver_age")
    set_lora(env.project, "style", None, env.lora, weight=0.85)

    meta = json.loads((env.project.assets_dir / "style" / "meta.json").read_text())
    assert meta["lora_path"] == str(env.lora)
    assert meta["lora_weight"] == 0.85


@_with_env
def test_set_lora_unregistered_raises(env):
    try:
        set_lora(env.project, "character", "GHOST", env.lora)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Expected FileNotFoundError")


@_with_env
def test_set_lora_default_weight_when_unset(env):
    register_character(env.project, "NOVA")  # no lora, weight stays null
    set_lora(env.project, "character", "NOVA", env.lora)  # no explicit weight

    meta = json.loads((env.project.assets_dir / "characters" / "NOVA" / "meta.json").read_text())
    assert meta["lora_weight"] == 0.7  # default for characters


@_with_env
def test_set_lora_preserves_weight_when_only_path_changes(env):
    register_character(env.project, "NOVA", lora_path=env.lora, lora_weight=0.6)
    other_lora = env.root / "other.safetensors"
    other_lora.write_bytes(b"x")

    set_lora(env.project, "character", "NOVA", other_lora)  # no weight arg

    meta = json.loads((env.project.assets_dir / "characters" / "NOVA" / "meta.json").read_text())
    assert meta["lora_path"] == str(other_lora)
    assert meta["lora_weight"] == 0.6  # unchanged


@_with_env
def test_set_lora_unknown_asset_type_raises(env):
    try:
        set_lora(env.project, "vehicle", "CAR", env.lora)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError")


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


@_with_env
def test_get_character_returns_dataclass(env):
    register_character(
        env.project, "NOVA",
        description="blue hair",
        reference_images=[env.img_dir / "nova1.png", env.img_dir / "nova2.png"],
        lora_path=env.lora,
        lora_weight=0.7,
    )
    char = get_character(env.project, "NOVA")
    assert isinstance(char, CharacterRef)
    assert char.identifier == "NOVA"
    assert char.visual_description == "blue hair"
    assert len(char.reference_images) == 2
    assert all(isinstance(p, Path) for p in char.reference_images)
    # Sorted for determinism.
    assert char.reference_images == sorted(char.reference_images)
    assert isinstance(char.lora, LoRAConfig)
    assert char.lora.path == env.lora
    assert char.lora.weight == 0.7


@_with_env
def test_get_character_unregistered_raises(env):
    try:
        get_character(env.project, "GHOST")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Expected FileNotFoundError")


@_with_env
def test_get_character_no_lora_returns_none(env):
    register_character(env.project, "NOVA")
    char = get_character(env.project, "NOVA")
    assert char.lora is None


@_with_env
def test_get_style_roundtrip(env):
    register_style(env.project, preset="noir", prompt_prefix="high contrast,",
                   lora_path=env.lora, lora_weight=0.8)
    style = get_style(env.project)
    assert isinstance(style, StyleAsset)
    assert style.preset == "noir"
    assert style.prompt_prefix == "high contrast,"
    assert isinstance(style.lora, LoRAConfig)
    assert style.lora.weight == 0.8


@_with_env
def test_get_style_unregistered_raises(env):
    try:
        get_style(env.project)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Expected FileNotFoundError")


@_with_env
def test_get_location_roundtrip(env):
    register_location(env.project, "warehouse_night", description="dark warehouse")
    loc = get_location(env.project, "warehouse_night")
    assert isinstance(loc, LocationAsset)
    assert loc.identifier == "warehouse_night"
    assert loc.description == "dark warehouse"
    assert loc.lora is None
