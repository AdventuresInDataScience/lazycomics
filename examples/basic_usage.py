"""End-to-end demo: every Phase 1 function called against placeholder data.

Run this from anywhere — it creates a self-contained scratch project in
a temp dir, walks every step of the pipeline, and prints the path to
the resulting ``.cbz``.

This script exists for two reasons:

1. **Sanity check.** After installing ``lazycomics``, run this to
   verify all pieces are in place.
2. **Worked example.** Each step shows how the public function is
   meant to be called and what it produces. Read top-to-bottom for the
   canonical usage shape.

The "generated panels" step (Phase 2, not yet implemented) is faked by
writing solid-colour PNGs to ``<project>/panels/``. Replace that block
with a real Wan2GP call when Phase 2 lands.

Requires:
    pip install lazycomics
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

import lazycomics as cc

# ---------------------------------------------------------------------------
# 0. Workspace
# ---------------------------------------------------------------------------

WORKSPACE = Path(tempfile.mkdtemp(prefix="lazycomics_demo_"))
print(f"Workspace: {WORKSPACE}")

# A two-page CBML script with a single character (NOVA) and a couple of
# layout types so we exercise both a splash page and a 2x2 grid.
CBML_TEXT = """\
## Demo Comic
aspect: 2:3
author: lazycomics example

PAGE preset:splash

PANEL A
loc: warehouse_night
chars: NOVA
shot: low angle, dramatic
mood: tense
> Nova steps into the loading bay, flashlight cutting through smoke.

PAGE preset:grid-2x2

PANEL A
loc: warehouse_night
chars: NOVA
shot: medium
> She freezes — something moved.

NOVA: "Hello?"

PANEL B
loc: warehouse_night
chars: NOVA
shot: closeup
mood: alarmed

NOVA: "I know you're there."

PANEL C
loc: warehouse_night
shot: wide
> A shadow detaches from the far wall.

[sfx top-right color:#ff2200] FOOTSTEPS

PANEL D
loc: warehouse_night
chars: NOVA
shot: extreme closeup
mood: terrified

NOVA: ~No. No, no, no.~
"""

cbml_path = WORKSPACE / "story.cbml"
cbml_path.write_text(CBML_TEXT)


# ---------------------------------------------------------------------------
# 1. Project
# ---------------------------------------------------------------------------

project = cc.create_project(
    "demo_comic", cbml_path, base_dir=WORKSPACE / "projects",
)
print(f"Project created at: {project.base_dir}")


# ---------------------------------------------------------------------------
# 2. Register assets
# ---------------------------------------------------------------------------

# Pretend the user has real reference photos. Here we synthesise them.
nova_ref = WORKSPACE / "nova_face.png"
Image.new("RGB", (640, 960), (180, 100, 100)).save(nova_ref)

style_ref = WORKSPACE / "style_sample.png"
Image.new("RGB", (640, 640), (200, 200, 120)).save(style_ref)

cc.register_character(
    project, "NOVA",
    description="young woman, short blue hair, leather jacket, cyberpunk look",
    reference_images=[nova_ref],
)
cc.register_style(
    project,
    prompt_prefix="silver age comic art, bold inks, halftone shading",
    reference_images=[style_ref],
)
print("Characters + style registered")


# ---------------------------------------------------------------------------
# 3. Enrich CBML → per-panel JSON
# ---------------------------------------------------------------------------

panels = cc.enrich(project)
print(f"Enriched: {len(panels)} panel(s) → {project.enriched_dir}/")
for p in panels:
    print(f"    {p.panel_id}  aspect={p.aspect_ratio:.3f}  loc={p.loc_identifier}")


# ---------------------------------------------------------------------------
# 4. Build prompts
# ---------------------------------------------------------------------------

prompts = cc.build_prompts(project)
print(f"Prompts: {len(prompts)} file(s) → {project.prompts_dir}/")
# Show the first one as a sample.
first_id = next(iter(prompts))
print(f"    {first_id}.txt:")
print(f"      {(project.prompts_dir / f'{first_id}.txt').read_text().strip()}")


# ---------------------------------------------------------------------------
# 5. (Optional) LLM-assisted prompt refinement
# ---------------------------------------------------------------------------

# Skipped here — requires a running LLM endpoint configured in
# lazycomics_config.yaml. Enable by adding:
#
#     llm:
#       url: http://localhost:11434/v1
#       model: llama3.1:8b
#
# and uncommenting:
# cc.refine_prompts_with_llm(project)


# ---------------------------------------------------------------------------
# 6. Prepare reference images
# ---------------------------------------------------------------------------

refs = cc.prepare_references(project, working_resolution=512)
print(f"Refs prepared: {len(refs)} panel(s) → {project.refs_prepared_dir}/")


# ---------------------------------------------------------------------------
# 7. *** Generate panels (Phase 2 bridge — not yet implemented) ***
#
# In a real run, Wan2GP would read each prompt + ref stack and write
# generated PNGs to <project>/panels/. We fake that here with solid
# colours so the rest of the pipeline has something to chew on.
# ---------------------------------------------------------------------------

PLACEHOLDER_COLOURS = [
    (180, 60, 60),   # red-ish
    (60, 120, 180),  # blue-ish
    (60, 160, 80),   # green-ish
    (200, 160, 60),  # amber
    (140, 80, 160),  # purple
]

for i, panel in enumerate(panels):
    colour = PLACEHOLDER_COLOURS[i % len(PLACEHOLDER_COLOURS)]
    target = project.panels_dir / f"{panel.panel_id}.png"
    Image.new("RGB", (1024, 1024), colour).save(target)
print(f"Placeholder panels written → {project.panels_dir}/")


# ---------------------------------------------------------------------------
# 8. Render text (bubbles + captions + SFX onto each panel)
# ---------------------------------------------------------------------------

rendered = cc.render_text(project)
print(f"Text rendered onto {len(rendered)} panel(s) → {project.panels_text_dir}/")


# ---------------------------------------------------------------------------
# 9. Assemble panels into pages
# ---------------------------------------------------------------------------

pages = cc.assemble_pages(project, page_height_px=1500, gutter_px=12)
print(f"Pages assembled: {len(pages)} → {project.pages_dir}/")
for page_num, path in pages.items():
    img = Image.open(path)
    print(f"    page {page_num:>2}: {path.name}  ({img.size[0]}×{img.size[1]})")


# ---------------------------------------------------------------------------
# 10. Export .cbz
# ---------------------------------------------------------------------------

cbz_path = cc.export_cbz(project)
print(f"CBZ exported: {cbz_path}")

with zipfile.ZipFile(cbz_path) as zf:
    print("    contents:")
    for name in zf.namelist():
        info = zf.getinfo(name)
        print(f"      {name}  ({info.file_size:,} bytes)")


# ---------------------------------------------------------------------------
# Wrap up
# ---------------------------------------------------------------------------

print()
print("=" * 60)
print(f"  Done. Open the .cbz with any comic reader:")
print(f"  {cbz_path}")
print("=" * 60)

# Uncomment to clean up the temp workspace; left in place by default so
# you can inspect the .cbz and the staged intermediates.
# shutil.rmtree(WORKSPACE)
