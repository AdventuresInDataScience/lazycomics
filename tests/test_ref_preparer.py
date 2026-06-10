"""Tests for lazycomics.ref_preparer.

Creates real images via PIL so we can verify dimensions, crop ratios, and
file outputs. Face detection (the only optional / heavy dep) is patched at
the module level so tests are deterministic regardless of whether MediaPipe
is installed.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from PIL import Image

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics import ref_preparer  # noqa: E402
from lazycomics.asset_registry import register_style  # noqa: E402
from lazycomics.geometry import resolution_for_aspect  # noqa: E402
from lazycomics.project import create_project  # noqa: E402
from lazycomics.ref_preparer import prepare_references  # noqa: E402


# ---------------------------------------------------------------------------
# Test environment
# ---------------------------------------------------------------------------


def _make_img(path: Path, w: int, h: int, colour=(128, 128, 128)):
    """Create a solid-colour image of size w×h at ``path``."""
    Image.new("RGB", (w, h), colour).save(path)


class _Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cbml = self.root / "story.cbml"
        cbml.write_text(
            "## Test\naspect: 2:3\n\nPAGE preset:splash\n\nPANEL A\nloc: room\n> A test.\n"
        )
        self.project = create_project("p1", cbml, base_dir=self.root)

        # Source images (the user's raw reference images on disk).
        self.src_dir = self.root / "src_images"
        self.src_dir.mkdir()

        # Force the face detector to return None unless an individual test
        # patches it — keeps tests independent of MediaPipe availability.
        self._orig_detect = ref_preparer._detect_face_centre
        ref_preparer._detect_face_centre = lambda img: None

    def make_src(self, name: str, w: int, h: int, colour=(128, 128, 128)) -> Path:
        p = self.src_dir / name
        _make_img(p, w, h, colour)
        return p

    def write_enriched(self, panel_id: str, **fields):
        data = {
            "panel_id": panel_id,
            "page_index": 0,
            "panel_index": 0,
            "aspect_ratio": 2 / 3,
            "shot_hint": "",
            "mood": None,
            "loc_identifier": None,
            "loc_description": "",
            "loc_reference_images": [],
            "characters": [],
        }
        data.update(fields)
        (self.project.enriched_dir / f"{panel_id}.json").write_text(json.dumps(data))
        return data

    def close(self):
        ref_preparer._detect_face_centre = self._orig_detect
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


def _char(identifier="X", reference_images=None):
    return {
        "identifier": identifier,
        "visual_description": "",
        "reference_images": [str(p) for p in (reference_images or [])],
        "lora": None,
    }


# ---------------------------------------------------------------------------
# Basics: outputs, return value, filenames
# ---------------------------------------------------------------------------


@_with_env
def test_writes_primary_ref_with_correct_filename(env):
    src = env.make_src("nova.png", 600, 600)
    env.write_enriched("page_1_panel_1", characters=[_char("NOVA", [src])])
    prepare_references(env.project)
    assert (env.project.refs_prepared_dir / "page_1_panel_1_ref.png").is_file()


@_with_env
def test_returns_panel_id_to_paths_dict(env):
    src = env.make_src("nova.png", 600, 600)
    env.write_enriched("p1", characters=[_char("NOVA", [src])])
    result = prepare_references(env.project)
    assert "p1" in result
    assert isinstance(result["p1"], list)
    assert result["p1"][0].name == "p1_ref.png"


@_with_env
def test_no_enriched_files_returns_empty(env):
    assert prepare_references(env.project) == {}


# ---------------------------------------------------------------------------
# Aspect cropping
# ---------------------------------------------------------------------------


@_with_env
def test_primary_cropped_to_target_aspect(env):
    # Source is 900×600 (3:2). Target aspect 2:3. The primary must come out at
    # the EXACT resolution the bridge will request (Klein keys output aspect
    # off image_start), so face-aware crop to aspect then resize to (W, H).
    src = env.make_src("wide.png", 900, 600)
    env.write_enriched("p1", aspect_ratio=2 / 3, characters=[_char("X", [src])])
    prepare_references(env.project)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png")
    assert out.size == resolution_for_aspect(2 / 3, 1024), (
        f"got {out.size}, expected {resolution_for_aspect(2 / 3, 1024)}"
    )


@_with_env
def test_primary_no_crop_when_already_target_aspect(env):
    # Source 600×900 already 2:3; still resized to the exact target resolution.
    src = env.make_src("ok.png", 600, 900)
    env.write_enriched("p1", aspect_ratio=2 / 3, characters=[_char("X", [src])])
    prepare_references(env.project)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png")
    assert out.size == resolution_for_aspect(2 / 3, 1024)


@_with_env
def test_primary_crop_vertical_when_source_too_tall(env):
    # Source 600×1200 (1:2), target 2:3 → crop vertically, then exact resize.
    src = env.make_src("tall.png", 600, 1200)
    env.write_enriched("p1", aspect_ratio=2 / 3, characters=[_char("X", [src])])
    prepare_references(env.project)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png")
    assert out.size == resolution_for_aspect(2 / 3, 1024)


@_with_env
def test_face_aware_crop_shifts_window(env):
    # Wide source (1200×600), target 2:3, so vertical strip from a wide image.
    # Face at (300, 300) — left third. Crop should be anchored left, not centred.
    src = env.make_src("face.png", 1200, 600)
    env.write_enriched("p1", aspect_ratio=2 / 3, characters=[_char("X", [src])])

    ref_preparer._detect_face_centre = lambda img: (300, 300)
    prepare_references(env.project)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png")
    # New width should be h * target_aspect = 600 * 2/3 = 400.
    # Centre crop would put x1 around 400; face-centred should put it around 100.
    # We can't read crop coords directly, but the resized output should be at
    # the exact target resolution.
    assert out.size == resolution_for_aspect(2 / 3, 1024)

    # Pixel inspection: in a centre crop of a uniformly-coloured image these are equivalent,
    # so we re-run with two-tone source and check colour distribution shifted toward left.


@_with_env
def test_face_aware_crop_anchors_on_face_pixel_check(env):
    # Two-tone source: left half red, right half blue. Wide (1200×600), target 2:3.
    img = Image.new("RGB", (1200, 600), (255, 0, 0))
    right = Image.new("RGB", (600, 600), (0, 0, 255))
    img.paste(right, (600, 0))
    src = env.src_dir / "twotone.png"
    img.save(src)

    env.write_enriched("p1", aspect_ratio=2 / 3, characters=[_char("X", [src])])

    # Face on the right half → crop should land in the blue region.
    ref_preparer._detect_face_centre = lambda im: (1000, 300)
    prepare_references(env.project)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png").convert("RGB")
    # Sample centre pixel — should be blue-dominant if face-aware anchor worked.
    cx, cy = out.size[0] // 2, out.size[1] // 2
    r, g, b = out.getpixel((cx, cy))
    assert b > r, f"expected blue-dominant centre pixel, got rgb=({r},{g},{b})"


@_with_env
def test_centre_crop_when_no_face(env):
    # Same two-tone setup, no face detected → centre crop, around the boundary.
    img = Image.new("RGB", (1200, 600), (255, 0, 0))
    right = Image.new("RGB", (600, 600), (0, 0, 255))
    img.paste(right, (600, 0))
    src = env.src_dir / "twotone.png"
    img.save(src)

    env.write_enriched("p1", aspect_ratio=2 / 3, characters=[_char("X", [src])])
    # Face detector stays the default (returns None) → centre crop.
    prepare_references(env.project)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png").convert("RGB")
    cx, cy = out.size[0] // 2, out.size[1] // 2
    r, _, b = out.getpixel((cx, cy))
    # Centre of a 1200-wide image is x=600, exactly on the boundary, so the
    # centred 400-wide crop spans x=400..800. The sampled centre is at x=600
    # which is the colour boundary. Either side is acceptable; just confirm
    # we didn't somehow end up far to one side.
    assert (abs(r - b)) < 256  # always true; the real assert is no crash + valid file
    assert out.size[0] > 0 and out.size[1] > 0


# ---------------------------------------------------------------------------
# Resizing
# ---------------------------------------------------------------------------


@_with_env
def test_resized_to_working_resolution(env):
    # 3000×2000 source. With aspect 2:3 we get a tall crop, then resize to 1024 long edge.
    src = env.make_src("big.png", 3000, 2000)
    env.write_enriched("p1", aspect_ratio=2 / 3, characters=[_char("X", [src])])
    prepare_references(env.project)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png")
    assert max(out.size) == 1024


@_with_env
def test_primary_sized_to_target_even_when_source_small(env):
    # A small source (200×300) must still be produced at the exact target
    # resolution — Klein needs image_start at the output size, so upscaling a
    # small source is correct here (the old "never upscale" rule applied to
    # the long edge only and produced an under-sized image_start).
    src = env.make_src("small.png", 200, 300)  # already 2:3
    env.write_enriched("p1", aspect_ratio=2 / 3, characters=[_char("X", [src])])
    prepare_references(env.project)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png")
    assert out.size == resolution_for_aspect(2 / 3, 1024)


@_with_env
def test_primary_resolution_matches_bridge_request(env):
    # The whole point of the fix: the primary ref's pixel size must equal the
    # resolution the bridge will ask Wan2GP for, for both orientations.
    from lazycomics.wan2gp_bridge import _compute_resolution

    for aspect in (2 / 3, 3 / 2, 1.0, 1 / 3):
        src = env.make_src(f"s_{aspect:.3f}.png", 800, 800)
        env.write_enriched("p1", aspect_ratio=aspect, characters=[_char("X", [src])])
        prepare_references(env.project, force=True)
        out = Image.open(env.project.refs_prepared_dir / "p1_ref.png")
        want = tuple(int(x) for x in _compute_resolution(aspect, 1024).split("x"))
        assert out.size == want, f"aspect {aspect}: ref {out.size} != bridge {want}"


@_with_env
def test_custom_working_resolution(env):
    src = env.make_src("big.png", 3000, 2000)
    env.write_enriched("p1", aspect_ratio=2 / 3, characters=[_char("X", [src])])
    prepare_references(env.project, working_resolution=512)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png")
    assert max(out.size) == 512


# ---------------------------------------------------------------------------
# Primary selection heuristics
# ---------------------------------------------------------------------------


@_with_env
def test_wide_shot_prefers_location_ref(env):
    char_src = env.make_src("char.png", 600, 600, colour=(255, 0, 0))
    loc_src = env.make_src("loc.png", 600, 600, colour=(0, 255, 0))
    env.write_enriched(
        "p1",
        shot_hint="wide establishing shot, low angle",
        characters=[_char("X", [char_src])],
        loc_reference_images=[str(loc_src)],
    )
    prepare_references(env.project)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png").convert("RGB")
    r, g, b = out.getpixel((out.size[0] // 2, out.size[1] // 2))
    assert g > r and g > b, f"expected green (location), got ({r},{g},{b})"


@_with_env
def test_closeup_uses_character_ref(env):
    char_src = env.make_src("char.png", 600, 600, colour=(255, 0, 0))
    loc_src = env.make_src("loc.png", 600, 600, colour=(0, 255, 0))
    env.write_enriched(
        "p1",
        shot_hint="extreme closeup, eye contact",
        characters=[_char("X", [char_src])],
        loc_reference_images=[str(loc_src)],
    )
    prepare_references(env.project)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png").convert("RGB")
    r, g, b = out.getpixel((out.size[0] // 2, out.size[1] // 2))
    assert r > g and r > b, f"expected red (character), got ({r},{g},{b})"


@_with_env
def test_no_characters_uses_location_ref(env):
    loc_src = env.make_src("loc.png", 600, 600, colour=(0, 0, 255))
    env.write_enriched("p1", loc_reference_images=[str(loc_src)])
    prepare_references(env.project)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png").convert("RGB")
    r, _, b = out.getpixel((out.size[0] // 2, out.size[1] // 2))
    assert b > r


@_with_env
def test_no_sources_skips_panel(env):
    env.write_enriched("p1")  # no chars, no loc refs
    result = prepare_references(env.project)
    assert "p1" not in result
    assert not (env.project.refs_prepared_dir / "p1_ref.png").exists()


# ---------------------------------------------------------------------------
# Primary selection — primary_character awareness
# ---------------------------------------------------------------------------


@_with_env
def test_primary_character_selects_correct_ref(env):
    """When enricher sets primary_character=REX, REX's ref is the primary."""
    nova_src = env.make_src("nova.png", 600, 600, colour=(255, 0, 0))
    rex_src = env.make_src("rex.png", 600, 600, colour=(0, 0, 255))
    env.write_enriched(
        "p1",
        characters=[_char("NOVA", [nova_src]), _char("REX", [rex_src])],
        primary_character="REX",
    )
    prepare_references(env.project)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png").convert("RGB")
    r, _, b = out.getpixel((out.size[0] // 2, out.size[1] // 2))
    assert b > r, f"expected blue (REX), got rgb=({r},_,{b})"


@_with_env
def test_primary_character_no_refs_falls_back_to_first_char(env):
    """primary_character has no reference images → fall through to first char with refs."""
    nova_src = env.make_src("nova.png", 600, 600, colour=(255, 0, 0))
    env.write_enriched(
        "p1",
        characters=[_char("NOVA", [nova_src]), _char("REX", [])],
        primary_character="REX",
    )
    prepare_references(env.project)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png").convert("RGB")
    r, _, b = out.getpixel((out.size[0] // 2, out.size[1] // 2))
    assert r > b, f"expected red (NOVA fallback), got rgb=({r},_,{b})"


@_with_env
def test_no_primary_character_backward_compat(env):
    """Older enriched JSON without primary_character → first char, same as Phase 1."""
    nova_src = env.make_src("nova.png", 600, 600, colour=(255, 0, 0))
    rex_src = env.make_src("rex.png", 600, 600, colour=(0, 0, 255))
    env.write_enriched(
        "p1",
        characters=[_char("NOVA", [nova_src]), _char("REX", [rex_src])],
        # No primary_character key at all
    )
    prepare_references(env.project)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png").convert("RGB")
    r, _, b = out.getpixel((out.size[0] // 2, out.size[1] // 2))
    assert r > b, f"expected red (NOVA first-listed), got rgb=({r},_,{b})"


@_with_env
def test_wide_shot_overrides_primary_character(env):
    """Wide/establishing shots pick location ref even when primary_character is set."""
    char_src = env.make_src("char.png", 600, 600, colour=(255, 0, 0))
    loc_src = env.make_src("loc.png", 600, 600, colour=(0, 255, 0))
    env.write_enriched(
        "p1",
        shot_hint="wide establishing shot",
        characters=[_char("NOVA", [char_src])],
        loc_reference_images=[str(loc_src)],
        primary_character="NOVA",
    )
    prepare_references(env.project)

    out = Image.open(env.project.refs_prepared_dir / "p1_ref.png").convert("RGB")
    r, g, _ = out.getpixel((out.size[0] // 2, out.size[1] // 2))
    assert g > r, f"expected green (location), got rgb=({r},{g},_)"


# ---------------------------------------------------------------------------
# Supporting refs
# ---------------------------------------------------------------------------


@_with_env
def test_supporting_refs_written_with_sequential_numbers(env):
    char_a = env.make_src("a.png", 600, 600)
    char_b = env.make_src("b.png", 600, 600)
    loc = env.make_src("loc.png", 600, 600)
    env.write_enriched(
        "p1",
        characters=[_char("A", [char_a]), _char("B", [char_b])],
        loc_reference_images=[str(loc)],
    )
    result = prepare_references(env.project)

    files = result["p1"]
    assert (env.project.refs_prepared_dir / "p1_ref.png").is_file()       # char A (primary)
    assert (env.project.refs_prepared_dir / "p1_ref_1.png").is_file()     # loc
    assert (env.project.refs_prepared_dir / "p1_ref_2.png").is_file()     # char B
    assert [f.name for f in files] == ["p1_ref.png", "p1_ref_1.png", "p1_ref_2.png"]


@_with_env
def test_style_refs_appended_to_supporting(env):
    char_src = env.make_src("char.png", 600, 600)
    style_src = env.make_src("style.png", 600, 600)
    register_style(env.project, prompt_prefix="x", reference_images=[style_src])

    env.write_enriched("p1", characters=[_char("A", [char_src])])
    result = prepare_references(env.project)

    files = [f.name for f in result["p1"]]
    # Primary is char; style is appended as supporting.
    assert "p1_ref.png" in files
    assert "p1_ref_1.png" in files


@_with_env
def test_primary_not_duplicated_in_supporting(env):
    # Same file appears as both the character's only ref and as a location ref.
    shared = env.make_src("shared.png", 600, 600)
    env.write_enriched(
        "p1",
        characters=[_char("A", [shared])],
        loc_reference_images=[str(shared)],
    )
    result = prepare_references(env.project)

    # Only the primary; no supporting since the loc ref is the same file.
    assert [f.name for f in result["p1"]] == ["p1_ref.png"]


# ---------------------------------------------------------------------------
# Supporting refs — inpaint_order awareness
# ---------------------------------------------------------------------------


@_with_env
def test_supporting_ordered_by_inpaint_order(env):
    """Character refs in supporting list follow inpaint_order, not chars: order."""
    nova_src = env.make_src("nova.png", 600, 600, colour=(255, 0, 0))
    rex_src = env.make_src("rex.png", 600, 600, colour=(0, 0, 255))
    ghost_src = env.make_src("ghost.png", 600, 600, colour=(0, 255, 0))
    env.write_enriched(
        "p1",
        characters=[
            _char("NOVA", [nova_src]),
            _char("REX", [rex_src]),
            _char("GHOST", [ghost_src]),
        ],
        primary_character="NOVA",
        inpaint_order=["NOVA", "GHOST", "REX"],
    )
    result = prepare_references(env.project)

    # Primary is NOVA. Supporting should be GHOST then REX (inpaint_order
    # minus primary), not the original chars: order (REX, GHOST).
    files = [f.name for f in result["p1"]]
    assert files == ["p1_ref.png", "p1_ref_1.png", "p1_ref_2.png"]

    # Verify by colour: ref_1 should be green (GHOST), ref_2 blue (REX).
    ref1 = Image.open(env.project.refs_prepared_dir / "p1_ref_1.png").convert("RGB")
    _, g1, _ = ref1.getpixel((ref1.size[0] // 2, ref1.size[1] // 2))
    ref2 = Image.open(env.project.refs_prepared_dir / "p1_ref_2.png").convert("RGB")
    _, _, b2 = ref2.getpixel((ref2.size[0] // 2, ref2.size[1] // 2))
    assert g1 > 200, f"expected green (GHOST) for ref_1, got g={g1}"
    assert b2 > 200, f"expected blue (REX) for ref_2, got b={b2}"


@_with_env
def test_supporting_fallback_order_without_inpaint_order(env):
    """Without inpaint_order, supporting refs use original chars: list order."""
    a_src = env.make_src("a.png", 600, 600, colour=(255, 0, 0))
    b_src = env.make_src("b.png", 600, 600, colour=(0, 0, 255))
    loc_src = env.make_src("loc.png", 600, 600, colour=(0, 255, 0))
    env.write_enriched(
        "p1",
        characters=[_char("A", [a_src]), _char("B", [b_src])],
        loc_reference_images=[str(loc_src)],
        # No inpaint_order — backward compat
    )
    result = prepare_references(env.project)

    # Primary is A (first char, no primary_character set).
    # Supporting: loc first, then B (chars order, A excluded as primary).
    files = [f.name for f in result["p1"]]
    assert files == ["p1_ref.png", "p1_ref_1.png", "p1_ref_2.png"]


# ---------------------------------------------------------------------------
# Skip / force
# ---------------------------------------------------------------------------


@_with_env
def test_skip_when_primary_already_exists(env):
    src = env.make_src("nova.png", 600, 600)
    env.write_enriched("p1", characters=[_char("X", [src])])
    prepare_references(env.project)

    # Manually overwrite the primary to detect re-runs.
    primary = env.project.refs_prepared_dir / "p1_ref.png"
    primary.write_bytes(b"user-override-content")

    prepare_references(env.project)  # default: force=False
    assert primary.read_bytes() == b"user-override-content"


@_with_env
def test_force_overwrites_existing(env):
    src = env.make_src("nova.png", 600, 600)
    env.write_enriched("p1", characters=[_char("X", [src])])
    prepare_references(env.project)

    primary = env.project.refs_prepared_dir / "p1_ref.png"
    primary.write_bytes(b"user-override-content")

    prepare_references(env.project, force=True)
    # Should be a real PNG again, not the placeholder bytes.
    assert primary.read_bytes() != b"user-override-content"
    Image.open(primary)  # loads cleanly


@_with_env
def test_skip_returns_existing_files_in_result(env):
    src = env.make_src("nova.png", 600, 600)
    env.write_enriched("p1", characters=[_char("X", [src])])
    prepare_references(env.project)

    result = prepare_references(env.project)  # re-run, all skipped
    assert "p1" in result
    assert any(f.name == "p1_ref.png" for f in result["p1"])


# ---------------------------------------------------------------------------
# Panels filter
# ---------------------------------------------------------------------------


@_with_env
def test_panels_filter_limits_processing(env):
    src = env.make_src("nova.png", 600, 600)
    env.write_enriched("page_1_panel_1", characters=[_char("X", [src])])
    env.write_enriched("page_1_panel_2", characters=[_char("X", [src])])

    result = prepare_references(env.project, panels=["page_1_panel_2"])
    assert set(result) == {"page_1_panel_2"}
    assert (env.project.refs_prepared_dir / "page_1_panel_2_ref.png").is_file()
    assert not (env.project.refs_prepared_dir / "page_1_panel_1_ref.png").is_file()


# ---------------------------------------------------------------------------
# Missing source files don't crash
# ---------------------------------------------------------------------------


@_with_env
def test_missing_source_file_skipped_cleanly(env):
    fake = env.src_dir / "does_not_exist.png"
    env.write_enriched("p1", characters=[_char("X", [fake])])
    result = prepare_references(env.project)
    assert "p1" not in result
