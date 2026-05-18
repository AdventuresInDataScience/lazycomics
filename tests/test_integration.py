"""Integration tests — real cbml_parser, multi-stage pipeline.

These complement the per-module tests by:

1. Exercising the real ``cbml_parser`` (not the ``SimpleNamespace`` mock
   seam used elsewhere) so any drift in the parser API or in our
   field-rename boundary surfaces here.
2. Running every Phase 1 stage end-to-end on a multi-page CBML with a
   spread, two characters, dialogue, captions, and SFX — to catch
   cross-stage contract drift that single-module tests can miss.

MediaPipe-backed seams (``_detect_face_centre``, ``_bubbles_should_go_top``)
are monkeypatched off to keep these tests deterministic and fast — the
per-module tests already cover those code paths thoroughly.
"""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics import ref_preparer, text_renderer  # noqa: E402
from lazycomics.assembler import assemble_pages  # noqa: E402
from lazycomics.asset_registry import register_character, register_style  # noqa: E402
from lazycomics.enricher import enrich  # noqa: E402
from lazycomics.exporter import export_cbz  # noqa: E402
from lazycomics.project import create_project  # noqa: E402
from lazycomics.prompt_builder import build_prompts  # noqa: E402
from lazycomics.ref_preparer import prepare_references  # noqa: E402
from lazycomics.text_renderer import render_text  # noqa: E402


# ---------------------------------------------------------------------------
# Test environment
# ---------------------------------------------------------------------------


class _Env:
    """Tmp workspace with a project. Patches MediaPipe seams to stay fast."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cbml_path = self.root / "story.cbml"

        # MediaPipe seams: covered in isolation by the per-module tests;
        # here we care about pipeline integration, not the detector.
        self._orig_face = ref_preparer._detect_face_centre
        self._orig_faces = text_renderer._detect_all_faces
        ref_preparer._detect_face_centre = lambda img: None
        text_renderer._detect_all_faces = lambda img: []

    def write_cbml(self, body: str) -> Path:
        self.cbml_path.write_text(body, encoding="utf-8")
        return self.cbml_path

    def make_project(self, name: str = "integration"):
        return create_project(name, self.cbml_path, base_dir=self.root)

    def make_image(self, path: Path, w: int = 600, h: int = 600,
                   colour=(128, 128, 128)) -> Path:
        Image.new("RGB", (w, h), colour).save(path)
        return path

    def close(self):
        ref_preparer._detect_face_centre = self._orig_face
        text_renderer._detect_all_faces = self._orig_faces
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
# Test 1 — enrich() against the real cbml_parser
# ---------------------------------------------------------------------------


@_with_env
def test_enrich_against_real_cbml_parser(env):
    """The full public ``enrich()`` path — real CBML, real parser, real enricher.

    The per-module enricher tests inject ``SimpleNamespace`` Comics into the
    testable seam (``_enrich_from_comic``). This one catches drift between
    our enricher and the actual ``cbml_parser`` API: any rename of parser
    fields, any change in v1.1 ``[sfx ...]`` / caption-attribute handling,
    or any aspect-tuple shape change will surface here.
    """
    env.write_cbml("""\
## Integration
aspect: 2:3

PAGE preset:strip-2

PANEL A
loc: room
chars: NOVA
shot: medium
mood: anxious
> Nova enters the room.
NOVA: "Hello?"
[caption pos:top-left bg:#1a1a2e color:#ffffff] Earlier...

PANEL B
loc: warehouse
chars: REX
shot: closeup
> Rex looks up.
[sfx pos:center color:#ff0000] CRASH!
""")
    project = env.make_project()

    panels = enrich(project)

    # Shape
    assert len(panels) == 2
    assert [p.panel_id for p in panels] == ["page_1_panel_1", "page_1_panel_2"]
    assert (project.enriched_dir / "page_1_panel_1.json").is_file()
    assert (project.enriched_dir / "page_1_panel_2.json").is_file()

    # Panel 1: dialogue + caption (v1.1 attributes)
    p1 = panels[0]
    assert p1.loc_identifier == "room"
    assert p1.action == "Nova enters the room."
    assert p1.shot_hint == "medium"
    assert p1.mood == "anxious"
    assert len(p1.characters) == 1
    assert p1.characters[0].identifier == "NOVA"
    assert len(p1.dialogue_lines) == 1
    assert p1.dialogue_lines[0].text == "Hello?"
    assert p1.dialogue_lines[0].bubble_type == "speech"
    # Parser's bg/color/pos -> our bg_color/text_color/position
    assert len(p1.caption_boxes) == 1
    cap = p1.caption_boxes[0]
    assert cap.text == "Earlier..."
    assert cap.bg_color == "#1a1a2e"
    assert cap.text_color == "#ffffff"
    assert cap.position == "top-left"

    # Panel 2: v1.1 sfx, field rename pos -> position
    p2 = panels[1]
    assert p2.loc_identifier == "warehouse"
    assert p2.action == "Rex looks up."
    assert p2.mood is None
    assert len(p2.sfx_lines) == 1
    sfx = p2.sfx_lines[0]
    assert sfx.text == "CRASH!"
    assert sfx.color == "#ff0000"
    assert sfx.position == "center"

    # Aspect ratio derived from real layout: strip-2 is a 2x1 grid on a 2:3
    # canvas, so each panel is 1 col x 1 row -> width 1, height 3 -> 1/3.
    assert abs(p1.aspect_ratio - (1 / 3)) < 1e-6
    assert abs(p2.aspect_ratio - (1 / 3)) < 1e-6


# ---------------------------------------------------------------------------
# Test 2 — full pipeline end-to-end on a multi-page CBML with a spread
# ---------------------------------------------------------------------------


@_with_env
def test_full_pipeline_two_pages_spread_two_chars(env):
    """Every Phase 1 stage in sequence on a non-trivial CBML.

    Catches cross-stage contract drift that single-module tests can miss:
    panel-id agreement between enricher / prompt_builder / ref_preparer /
    render_text / assembler, asset registry plumbing through into prompts,
    and spread page-numbering all the way from parse to .cbz contents.

    Shape: 2 logical pages (strip-2 + spread:2) -> 3 physical pages, 3
    panels. Two registered characters, one with reference image. One
    caption, one SFX, two dialogue lines.
    """
    env.write_cbml("""\
## E2E Integration
aspect: 2:3

PAGE preset:strip-2

PANEL A
loc: room
chars: NOVA
shot: medium
> Nova checks the door.
NOVA: "Is anyone there?"
[caption pos:top-left bg:#000000 color:#ffffff] Later that night.

PANEL B
loc: room
chars: REX
shot: closeup
mood: tense
> Rex hides behind the crate.
[sfx pos:center color:#ff0000] CRACK!

PAGE spread:2

PANEL A
loc: warehouse
chars: NOVA, REX
shot: wide establishing
> The warehouse opens up before them.
NOVA: "Stay close."
""")
    project = env.make_project()

    # ---- assets ----------------------------------------------------------
    nova_ref = env.make_image(env.root / "nova.png", colour=(180, 100, 100))
    rex_ref = env.make_image(env.root / "rex.png", colour=(100, 100, 180))
    style_ref = env.make_image(env.root / "style.png", colour=(200, 200, 120))

    register_character(project, "NOVA",
                       description="young woman, blue hair",
                       reference_images=[nova_ref])
    register_character(project, "REX",
                       description="middle-aged man, bald, scarred",
                       reference_images=[rex_ref])
    register_style(project,
                   prompt_prefix="silver age comic art, bold inks",
                   reference_images=[style_ref])

    # ---- stage 1: enrich -------------------------------------------------
    panels = enrich(project)
    panel_ids = [p.panel_id for p in panels]
    assert panel_ids == [
        "page_1_panel_1", "page_1_panel_2",   # strip-2
        "page_2_panel_1",                       # spread
    ]
    # Registered description plumbed through from asset registry.
    assert panels[0].characters[0].visual_description == "young woman, blue hair"
    assert panels[2].characters[0].identifier == "NOVA"
    assert panels[2].characters[1].identifier == "REX"

    # ---- stage 2: prompts ------------------------------------------------
    prompts = build_prompts(project)
    assert set(prompts) == set(panel_ids)
    for pid in panel_ids:
        pos, neg = prompts[pid]
        # Style prefix appears in every prompt (registered globally).
        assert "silver age comic art" in pos
        # Negative prompt is the default and non-empty.
        assert neg

    # ---- stage 3: refs ---------------------------------------------------
    refs = prepare_references(project, working_resolution=256)
    assert set(refs) == set(panel_ids)
    for pid in panel_ids:
        assert (project.refs_prepared_dir / f"{pid}_ref.png").is_file()

    # ---- stage 4: drop placeholder generated panels ----------------------
    # In Phase 2 Wan2GP writes these; here we synthesise distinct colours.
    placeholder_colours = [(180, 60, 60), (60, 120, 180), (60, 160, 80)]
    for pid, colour in zip(panel_ids, placeholder_colours):
        Image.new("RGB", (800, 1200), colour).save(
            project.panels_dir / f"{pid}.png"
        )

    # ---- stage 5: render text overlays -----------------------------------
    rendered = render_text(project)
    assert set(rendered) == set(panel_ids)
    for pid in panel_ids:
        assert (project.panels_text_dir / f"{pid}.png").is_file()

    # ---- stage 6: assemble pages -----------------------------------------
    # spread:2 on logical page 2 consumes physical pages 2 and 3 -> 3 total.
    pages = assemble_pages(project, page_height_px=900, gutter_px=10)
    assert set(pages) == {1, 2, 3}
    for n in (1, 2, 3):
        out_path = project.pages_dir / f"page_{n:03d}.png"
        assert out_path.is_file()
        # Each physical page is 2:3 at h=900 -> 600 wide. Spread halves are
        # split at the midline so each output is one physical page.
        assert Image.open(out_path).size == (600, 900), f"page {n} size"

    # ---- stage 7: export .cbz --------------------------------------------
    cbz = export_cbz(project)
    assert cbz.is_file()
    assert cbz.suffix == ".cbz"
    with zipfile.ZipFile(cbz) as zf:
        names = zf.namelist()
    assert names == ["page_001.png", "page_002.png", "page_003.png"]
