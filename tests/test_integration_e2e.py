"""End-to-end integration test — REAL pipeline, no mocks.

Runs the full lazycomics pipeline against the real external tools
(Wan2GP, Real-ESRGAN, Ollama). The only difference from a production run
is that each stage asserts correctness before proceeding.

Prerequisites:
    - lazycomics + cbml_parser + mediapipe installed
    - lazycomics_config.yaml in repo root with wan2gp paths
    - Wan2GP model weights downloaded (via Pinokio)
    - Pinokio NOT actively running Wan2GP (avoids GPU contention)
    - (Optional) realesrgan-ncnn-vulkan on PATH — upscale stage skipped if absent
    - (Optional) Ollama running at localhost:11434 — LLM stage skipped if absent

Run:
    pytest tests/test_integration_e2e.py -v -s

Output is left in ./test_e2e_output/ for visual inspection.
"""

from __future__ import annotations

import shutil
import sys
import time
import zipfile
from pathlib import Path

import pytest
from PIL import Image

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

import lazycomics as lc  # noqa: E402
from lazycomics.config import load_config  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "integration"
OUTPUT_ROOT = Path(__file__).parent.parent / "test_e2e_output"


# ---------------------------------------------------------------------------
# Real fixtures
# ---------------------------------------------------------------------------


def _tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def _ollama_reachable() -> bool:
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _create_ref_images(workspace: Path) -> dict[str, Path]:
    """Real PNG reference images (solid colours, small but valid)."""
    refs: dict[str, Path] = {}
    for name, size, colour in [
        ("nova_ref",  (256, 384), (200,  80,  80)),
        ("rex_ref",   (256, 384), ( 80,  80, 200)),
        ("alley_ref", (384, 256), ( 40,  80,  40)),
        ("style_ref", (256, 256), (180, 160, 100)),
    ]:
        p = workspace / f"{name}.png"
        Image.new("RGB", size, colour).save(p)
        refs[name] = p
    return refs


def _setup_project(name: str, refs: dict[str, Path]):
    """Build a project with the real story.cbml and registered assets."""
    project_root = OUTPUT_ROOT / name
    if project_root.exists():
        shutil.rmtree(project_root)
    project_root.mkdir(parents=True)

    cbml_dst = project_root / "story.cbml"
    shutil.copy2(FIXTURES / "story.cbml", cbml_dst)

    project = lc.create_project(name, cbml_dst, base_dir=project_root)

    lc.register_character(
        project, "NOVA",
        description="young woman, short blue hair, leather jacket, cyberpunk look",
        reference_images=[refs["nova_ref"]],
    )
    lc.register_character(
        project, "REX",
        description="grizzled older man, grey beard, trenchcoat, scarred face",
        reference_images=[refs["rex_ref"]],
    )
    lc.register_location(
        project, "steampunk_alley",
        description="narrow steampunk alley, brass pipes, yellow gaslight, cobblestones",
        reference_images=[refs["alley_ref"]],
    )
    lc.register_style(
        project,
        prompt_prefix="noir comic art, heavy shadows, dramatic contrast",
        reference_images=[refs["style_ref"]],
    )
    return project


# ---------------------------------------------------------------------------
# THE end-to-end pipeline test
# ---------------------------------------------------------------------------


def test_full_pipeline_e2e():
    """Drive every stage of the pipeline against real Wan2GP.

    Stages:
        enrich → build_prompts → prepare_references → (LLM refine, optional)
        → generate_panels → skip-if-exists check → render_text → assemble_pages
        → (upscale, optional) → export_cbz
    """
    # --- Clean output tree ---
    if OUTPUT_ROOT.exists():
        shutil.rmtree(OUTPUT_ROOT)
    OUTPUT_ROOT.mkdir(parents=True)

    # --- Real config (fails fast if lazycomics_config.yaml is missing) ---
    config = load_config()
    wgp = config.get("wan2gp", {}).get("wgp_root")
    if not wgp or not Path(wgp).exists():
        pytest.skip(
            f"wan2gp.wgp_root not configured or not present: {wgp!r}. "
            "Edit lazycomics_config.yaml to point at your Pinokio Wan2GP install."
        )

    # --- Real reference images ---
    fixture_dir = OUTPUT_ROOT / "_fixtures"
    fixture_dir.mkdir()
    refs = _create_ref_images(fixture_dir)

    # --- Project with the real story.cbml + registered assets ---
    project = _setup_project("e2e", refs)

    # === Enrich =============================================================
    panels = lc.enrich(project)
    assert len(panels) == 5, f"expected 5 panels, got {len(panels)}"
    by_id = {p.panel_id: p for p in panels}

    p1a = by_id["page_1_panel_1"]
    assert p1a.char_generation_strategy == "single"
    assert p1a.primary_character == "NOVA"
    assert len(p1a.dialogue_lines) > 0

    p2a = by_id["page_2_panel_1"]
    assert p2a.char_generation_strategy == "multi_inpaint", \
        f"NOVA+REX talking should be multi_inpaint, got {p2a.char_generation_strategy!r}"
    assert len(p2a.inpaint_order) == 2
    assert len(p2a.bubble_layout) == 2

    p2b = by_id["page_2_panel_2"]
    assert p2b.char_generation_strategy == "single"

    p2c = by_id["page_2_panel_3"]
    assert len(p2c.characters) == 0
    assert len(p2c.sfx_lines) > 0, "PITTER-PATTER SFX should be parsed"

    p2d = by_id["page_2_panel_4"]
    assert p2d.char_generation_strategy == "single", \
        f"fighting must auto-downgrade from multi_inpaint, got {p2d.char_generation_strategy!r}"

    # === Build prompts ======================================================
    prompts = lc.build_prompts(project)
    assert len(prompts) == 5
    for pid in by_id:
        txt = project.prompts_dir / f"{pid}.txt"
        assert txt.is_file() and txt.stat().st_size > 10, f"prompt empty: {pid}"

    # === Prepare references =================================================
    refs_result = lc.prepare_references(project, working_resolution=256)
    assert len(refs_result) >= 3, f"expected >=3 prepared refs, got {len(refs_result)}"

    nova_primary = project.refs_prepared_dir / "page_1_panel_1_ref.png"
    assert nova_primary.is_file()
    assert Image.open(nova_primary).size[0] > 0

    # === LLM refinement (optional) ==========================================
    if _ollama_reachable() and config.get("llm", {}).get("url"):
        orig = (project.prompts_dir / "page_1_panel_1.txt").read_text()
        refined = lc.refine_prompts_with_llm(project)
        assert len(refined) > 0, "LLM should refine at least one prompt"
        new_txt = (project.prompts_dir / "page_1_panel_1.txt").read_text()
        assert new_txt != orig, "LLM-refined prompt should differ from original"
        assert len(list(project.prompts_dir.glob("*.bak"))) > 0, \
            "refinement should leave .bak backups"
    else:
        print("\n  [skip] LLM refinement — Ollama not reachable at localhost:11434")

    # === Generate panels (REAL Wan2GP — slow) ===============================
    gen = lc.generate_panels(project, force=True, config=config)
    assert len(gen) == 5, f"expected 5 panels generated, got {len(gen)}: {list(gen.keys())}"

    for pid in by_id:
        p = project.panels_dir / f"{pid}.png"
        assert p.is_file(), f"panel image missing: {pid}"
        img = Image.open(p)
        assert img.size[0] > 50 and img.size[1] > 50, \
            f"panel {pid} too small: {img.size}"

    # Inpaint masks should exist for the multi-inpaint panel
    mask_dir = project.panels_dir / "_masks"
    masks = list(mask_dir.glob("page_2_panel_1_mask_*.png")) if mask_dir.exists() else []
    assert len(masks) > 0, "multi-inpaint panel should produce inpaint masks"

    # === Skip-if-exists: re-run without force, confirm nothing changes =====
    mtimes_before = {p.name: p.stat().st_mtime
                     for p in project.panels_dir.glob("*.png")}
    time.sleep(0.1)
    gen_again = lc.generate_panels(project, force=False, config=config)
    assert len(gen_again) == 5
    mtimes_after = {p.name: p.stat().st_mtime
                    for p in project.panels_dir.glob("*.png")}
    assert mtimes_before == mtimes_after, \
        "skip-if-exists must not re-write existing panel files"

    # === Render text ========================================================
    rendered = lc.render_text(project)
    assert len(rendered) == 5

    # === Assemble pages =====================================================
    pages = lc.assemble_pages(project, page_height_px=800, gutter_px=8)
    assert len(pages) == 2, f"expected 2 pages, got {len(pages)}"
    for pnum in (1, 2):
        ppath = project.pages_dir / f"page_{pnum}.png"
        assert ppath.is_file()
        img = Image.open(ppath)
        assert img.size[0] > 100 and img.size[1] > 100, \
            f"page {pnum} too small: {img.size}"

    # === Upscale (optional) =================================================
    upscaler_exe = config.get("upscaler", {}).get("executable", "realesrgan-ncnn-vulkan")
    if _tool_available(upscaler_exe):
        upscaled = lc.upscale_pages(project, force=True, config=config)
        assert len(upscaled) >= 2, f"expected 2 upscaled pages, got {len(upscaled)}"
        for path in upscaled:
            assert path.is_file()
            img = Image.open(path)
            assert img.size[0] > 800 or img.size[1] > 800, \
                f"{path.name} doesn't look upscaled: {img.size}"
    else:
        print(f"\n  [skip] Upscale — '{upscaler_exe}' not on PATH")

    # === Export CBZ =========================================================
    cbz = lc.export_cbz(project)
    assert cbz.is_file()
    with zipfile.ZipFile(cbz) as zf:
        names = zf.namelist()
        assert any("page_" in n for n in names), \
            f"CBZ missing page images: {names}"

    print(f"\n  E2E output: {project.base_dir.resolve()}")
