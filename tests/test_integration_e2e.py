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


# ---------------------------------------------------------------------------
# Pipeline 
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

    # --- Project with the real story.cbml + registered assets ---
    project_root = OUTPUT_ROOT / "e2e"
    if project_root.exists():
        shutil.rmtree(project_root)
    project_root.mkdir(parents=True)

    cbml_dst = project_root / "story.cbml"
    shutil.copy2(FIXTURES / "story.cbml", cbml_dst)

    project = lc.create_project("e2e", cbml_dst, base_dir=project_root)

    # Register Characters
    for char_name in ["DAVID", "GERALD", "INTERN", "RANJEET", "YUKI"]:
        txt_path = FIXTURES / f"{char_name}.txt"
        png_path = FIXTURES / f"{char_name}.png"
        ref_images = [png_path] if png_path.exists() else []
        if txt_path.exists():
            desc = txt_path.read_text().strip()
            lc.register_character(
                project, char_name,
                description=desc,
                reference_images=ref_images,
            )

    # Register Locations
    for loc_name in ["GERALDS_APARTMENT", "THE_BAR", "THE_OFFICE"]:
        txt_path = FIXTURES / f"{loc_name}.txt"
        png_path = FIXTURES / f"{loc_name}.png"
        ref_images = [png_path] if png_path.exists() else []
        if txt_path.exists():
            desc = txt_path.read_text().strip()
            lc.register_location(
                project, loc_name,
                description=desc,
                reference_images=ref_images,
            )

    # Register Style
    style_txt_path = FIXTURES / "Style.txt"
    style_png_path = FIXTURES / "Style.png"
    if style_txt_path.exists():
        desc = style_txt_path.read_text().strip()
        ref_images = [style_png_path] if style_png_path.exists() else []
        lc.register_style(
            project,
            prompt_prefix=desc,
            reference_images=ref_images,
        )

    # === Enrich =============================================================
    panels = lc.enrich(project)
    assert len(panels) > 0, "expected panels to be parsed and enriched"
    by_id = {p.panel_id: p for p in panels}

    # === Build prompts ======================================================
    prompts = lc.build_prompts(project)
    assert len(prompts) == len(panels)
    for pid in by_id:
        txt = project.prompts_dir / f"{pid}.txt"
        assert txt.is_file() and txt.stat().st_size > 10, f"prompt empty: {pid}"

    # === Prepare references =================================================
    refs_result = lc.prepare_references(project, working_resolution=256)
    assert len(refs_result) >= 0, "references should be prepared"

    # === LLM refinement (optional) ==========================================
    if _ollama_reachable() and config.get("llm", {}).get("url"):
        first_panel_id = panels[0].panel_id
        orig = (project.prompts_dir / f"{first_panel_id}.txt").read_text()
        refined = lc.refine_prompts_with_llm(project)
        assert len(refined) > 0, "LLM should refine at least one prompt"
        new_txt = (project.prompts_dir / f"{first_panel_id}.txt").read_text()
        assert new_txt != orig, "LLM-refined prompt should differ from original"
        assert len(list(project.prompts_dir.glob("*.bak"))) > 0, \
            "refinement should leave .bak backups"
    else:
        print("\n  [skip] LLM refinement — Ollama not reachable at localhost:11434")

    # === Generate panels (REAL Wan2GP — slow) ===============================
    gen = lc.generate_panels(project, force=True, config=config)
    assert len(gen) == len(panels), f"expected {len(panels)} panels generated, got {len(gen)}"

    for pid in by_id:
        p = project.panels_dir / f"{pid}.png"
        assert p.is_file(), f"panel image missing: {pid}"
        img = Image.open(p)
        assert img.size[0] > 50 and img.size[1] > 50, \
            f"panel {pid} too small: {img.size}"

    # === Skip-if-exists: re-run without force, confirm nothing changes =====
    mtimes_before = {p.name: p.stat().st_mtime
                     for p in project.panels_dir.glob("*.png")}
    time.sleep(0.1)
    gen_again = lc.generate_panels(project, force=False, config=config)
    assert len(gen_again) == len(panels)
    mtimes_after = {p.name: p.stat().st_mtime
                    for p in project.panels_dir.glob("*.png")}
    assert mtimes_before == mtimes_after, \
        "skip-if-exists must not re-write existing panel files"

    # === Render text ========================================================
    rendered = lc.render_text(project)
    assert len(rendered) == len(panels)

    # === Assemble pages =====================================================
    pages = lc.assemble_pages(project, page_height_px=800, gutter_px=8)
    assert len(pages) > 0, "expected pages to be assembled"
    for pnum in range(1, len(pages) + 1):
        # Pages are written zero-padded (page_001.png), matching the
        # assembler, exporter, and the rest of the test suite.
        ppath = project.pages_dir / f"page_{pnum:03d}.png"
        assert ppath.is_file(), f"expected assembled page at {ppath}"
        img = Image.open(ppath)
        assert img.size[0] > 100 and img.size[1] > 100, \
            f"page {pnum} too small: {img.size}"

    # === Upscale (optional) =================================================
    upscaler_exe = config.get("upscaler", {}).get("executable", "realesrgan-ncnn-vulkan")
    if _tool_available(upscaler_exe):
        upscaled = lc.upscale_pages(project, force=True, config=config)
        assert len(upscaled) == len(pages), f"expected {len(pages)} upscaled pages, got {len(upscaled)}"
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
