# Comic Creator — Implementation Plan
**Version:** 1.0
**Status:** Planning complete, implementation not yet started
**Last updated:** 2026-04

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Design Principles](#2-design-principles)
3. [Technology Stack](#3-technology-stack)
4. [Repository Structure](#4-repository-structure)
5. [Hardware & Environment](#5-hardware--environment)
6. [Pipeline Architecture](#6-pipeline-architecture)
7. [Stage-by-Stage Implementation Plan](#7-stage-by-stage-implementation-plan)
8. [Build Modes](#8-build-modes)
9. [Project File Structure (Runtime)](#9-project-file-structure-runtime)
10. [CBML Spec Notes](#10-cbml-spec-notes)
11. [Deferred / Future Extensions](#11-deferred--future-extensions)
12. [Implementation Order](#12-implementation-order)
13. [Package Architecture Decision](#13-package-architecture-decision)

---

## 1. Project Overview

A local Python desktop application that takes a `.cbml` file (Comic Book Markup Language)
plus user-supplied reference assets, and generates a complete comic book exported as a `.cbz`
file. The user writes the story and provides reference images; the app handles all visual
generation.

**Key characteristics:**
- Fully local — no cloud API required in the critical path
- GPU-accelerated image generation via diffusers (SDXL or FLUX)
- Three build modes: Draft (fast/low quality), Production (slow/high quality), Custom
- Character and style consistency via LoRA or IP-Adapter (user's choice per asset)
- Deterministic enrichment layer — all mechanical decisions made in pure Python before
  any model inference occurs
- Resumable — each stage caches its outputs so a failed run can continue mid-comic

---

## 2. Design Principles

### 2.1 Separation of concerns

The pipeline has four conceptually distinct layers that must not bleed into each other:

| Layer | What it does | Tools |
|---|---|---|
| Parsing | Reads CBML into structured objects | cbml_parser (pip package) |
| Enrichment | Derives all mechanical decisions | Pure Python |
| Prompt building | Assembles text prompts | Pure Python string functions |
| Generation | Produces images | diffusers, LoRA, ControlNet etc |

No enrichment logic should appear in the generator. No generation calls should appear
in the enricher. The PanelGenerationRequest dataclass is the contract between layers.

### 2.2 LLM is optional, not required

The critical path contains zero LLM inference calls. Prompt construction is pure string
assembly. LLM-enhanced prompt generation is a future optional toggle that improves
quality but is never required to run the pipeline.

### 2.3 Mechanical before inferential

Anything that can be derived deterministically from the CBML structure (aspect ratios,
character counts, bubble regions, bleed flags, shot fallbacks) must be. This reduces
latency, cost, and unpredictability.

### 2.4 Resumability

Every stage writes its outputs to a cache/ directory within the project folder. If a
run fails, the runner checks what is already cached and skips completed work. Essential
given generation times of 90s+ per panel.

### 2.5 Quality/speed axis

Every significant pipeline decision has a fast and a slow option. These are surfaced
as build modes rather than scattered individual toggles, so the UX remains simple.

### 2.6 Integrated architecture

The pipeline is kept as a single integrated application rather than separate packages.
cbml_parser is the sole external package — it has genuine independent utility, a clean
public API, and no pipeline dependencies. No other component meets this bar. Complexity
is managed by strict dataclass contracts (models.py), a single orchestrating runner
(runner.py), and the cache layer as a natural seam between stages.

---

## 3. Technology Stack

| Layer | Choice | Rationale / Caveats |
|---|---|---|
| UI | Gradio | Fastest to build for ML pipelines; good progress/streaming support; browser-based. Alternative: PyQt6 for native desktop feel later. |
| CBML Parser | cbml_parser (pip) | Already built. Import directly, no wrapper module needed. |
| Image generation | diffusers (HuggingFace) | Industry standard for local SDXL/FLUX. Model offloading via enable_model_cpu_offload() available if needed. |
| Base model | SDXL (draft) / FLUX.1 (production) | SDXL: faster, lower VRAM, mature LoRA ecosystem. FLUX: higher quality, slower, higher VRAM, less mature LoRA tooling. SDXL is v1 primary target. |
| Style & character consistency | LoRA + IP-Adapter | LoRA: best consistency, requires training (10-30 min per asset). IP-Adapter: no training, faster, slightly less consistent. User chooses per asset. |
| LoRA training | kohya_ss (subprocess) | Standard SDXL LoRA training tool. Significant setup complexity — wrap as pre-configured subprocess call using sd-scripts directly. |
| Pose control | ControlNet (OpenPose) | Controls body pose within a single panel. NOT a cross-panel consistency tool. |
| Depth ControlNet | ControlNet (MiDaS depth) | Used during multi-character inpaint loops to preserve 3D spatial coherence. |
| Segmentation | SAM (Segment Anything) | Used during inpaint loops to mask character regions. ~2.5GB model — load once, keep in memory. |
| Face detection | MediaPipe | Lightweight CPU, ~500ms. Informs bubble placement only. Alternative: RetinaFace. |
| Bubble/caption/SFX | Pillow | All text overlay. Full capability for all four CBML bubble types. Cairo deferred for per-glyph SFX transforms. |
| Upscaling | Pillow LANCZOS / Real-ESRGAN anime | Anime model chosen specifically — better for stylised comic art. Applied to full page, not individual panels. |
| Export | Python zipfile | CBZ is a ZIP of numbered PNGs. ZIP_STORED — no recompression of already-compressed PNGs. |
| Hardware detection | torch + psutil | Detects VRAM, RAM, GPU at startup. Sets pipeline defaults. |

---

## 4. Repository Structure

```
comic_creator/
│
├── app.py
├── config.py
├── requirements.txt
├── IMPLEMENTATION_PLAN.md
├── README.md
│
├── core/
│   ├── __init__.py
│   ├── models.py                   # ALL dataclasses — single source of truth
│   ├── asset_registry.py
│   └── hardware_profile.py
│
├── pipeline/
│   ├── __init__.py
│   ├── enricher.py
│   ├── prompt_builder.py
│   ├── image_generator.py
│   ├── controlnet_manager.py
│   ├── lora_manager.py
│   ├── ip_adapter_manager.py
│   ├── inpaint_manager.py
│   ├── text_renderer.py
│   ├── assembler.py
│   ├── upscaler.py
│   ├── exporter.py
│   └── runner.py
│
├── ui/
│   ├── __init__.py
│   ├── state.py
│   └── components/
│       ├── cbml_upload.py
│       ├── style_panel.py
│       ├── character_panel.py
│       ├── location_panel.py
│       ├── settings_panel.py
│       └── build_panel.py
│
├── assets/
│   ├── styles/
│   │   ├── silver_age.json
│   │   ├── manga.json
│   │   ├── noir.json
│   │   └── watercolour.json
│   ├── fonts/
│   │   ├── bangers.ttf
│   │   └── badaboom.ttf
│   └── pose_library/
│       ├── wide_shot.json
│       ├── medium_shot.json
│       ├── closeup.json
│       ├── two_shot.json
│       └── action_full_body.json
│
├── projects/                       # gitignored
│
└── tests/
    ├── test_enricher.py
    ├── test_prompt_builder.py
    ├── test_assembler.py
    ├── test_text_renderer.py
    └── fixtures/
        ├── minimal.cbml
        ├── multi_page.cbml
        └── sample_assets/
```

Note: cbml_parser is a pip package — no local cbml/ module needed.

---

## 5. Hardware & Environment

### Target development hardware
- GPU: Mobile RTX 3080 16GB Max-Q (~20-30% slower than desktop equivalent)
- RAM: 96GB DDR4
- Storage: SSD recommended

### Memory requirements

| Configuration | Min VRAM | Min RAM |
|---|---|---|
| Absolute minimum (IP-Adapter, no LoRA training) | 8GB | 16GB |
| Comfortable (SDXL + ControlNet + IP-Adapter) | 12GB | 32GB |
| Recommended (full pipeline, LoRA training) | 16GB | 40GB |
| Target hardware | 16GB | 96GB |

RAM advantage: 96GB allows all models to stay loaded simultaneously. The runner
detects available RAM and sets keep_models_loaded accordingly.

### Indicative generation times (mobile RTX 3080 16GB Max-Q)

| Operation | Estimated time |
|---|---|
| Single-character panel (SDXL + LoRA + ControlNet) | ~90s |
| Each additional character (inpaint pass) | ~35s |
| SAM segmentation | ~3s |
| Pillow text rendering (per panel) | <1s |
| Page stitching | <2s |
| Real-ESRGAN upscale (per page) | ~15s |
| LoRA training (per character/style asset) | 10-30 min |
| 24-panel comic (mostly single-char, production mode) | ~45-60 min |

### Hardware profile detection (core/hardware_profile.py)

Runs at startup. Sets recommended defaults for:
- Base model: FLUX if VRAM >= 16GB, else SDXL
- Character method: LoRA if VRAM >= 16GB, else IP-Adapter
- Upscaler: Real-ESRGAN if VRAM >= 12GB, else Lanczos
- Attention slicing: enabled if VRAM < 12GB
- CPU offload: enabled if VRAM < 10GB
- Keep models loaded: enabled if RAM >= 48GB

These are pre-populated defaults, not hard overrides. User can override in Custom mode.

---

## 6. Pipeline Architecture

```
CBML file + assets
       |
       v
[1. CBML PARSER]          cbml_parser (pip)
       |                  -> Comic, Page, Panel objects
       v
[2. ENRICHER]             Pure Python, no ML
       |                  -> PanelGenerationRequest per panel
       v
[3. PROMPT BUILDER]       Pure string assembly, no ML
       |                  -> text prompt + negative prompt per panel
       v
[4. IMAGE GENERATOR]      diffusers (SDXL or FLUX)
       |                  inputs: prompt, style LoRA/IP-Adapter,
       |                          character LoRA(s)/IP-Adapter(s), ControlNet
       |                  -> raw panel image (blank text areas)
       v
[5. INPAINT MANAGER]      multi-character panels only
       |                  SAM masks + depth ControlNet + inpaint passes
       |                  -> panel image with all characters present
       v
[6. TEXT RENDERER]        Pillow + MediaPipe
       |                  face detection -> bubble placement
       |                  renders: captions, bubbles, SFX
       |                  -> fully finished sealed panel image
       v
[7. ASSEMBLER]            Pillow geometry
       |                  stitches panels into page canvas
       |                  applies gutters, bleed edges, page border
       |                  -> one page PNG per CBML page block
       v
[8. UPSCALER]             Lanczos or Real-ESRGAN anime
       |                  applied to full page, not individual panels
       |                  -> final high-res page PNGs
       v
[9. EXPORTER]             Python zipfile
                          -> .cbz
```

---

## 7. Stage-by-Stage Implementation Plan

### 7.1 core/models.py

Build this first. Every other module imports from it. No dependencies.

Key dataclasses:

```python
@dataclass
class DialogueLine:
    character: str
    text: str
    bubble_type: str       # "speech" | "thought" | "shout" | "whisper"

@dataclass
class CaptionBox:
    text: str
    bg_color: str          # hex, default "#000000"
    text_color: str        # hex, default "#ffffff"
    position: str          # "top-left" | "top-center" | etc

@dataclass
class BubbleRegion:
    character: str
    x_frac: float
    y_frac: float
    width_frac: float
    height_frac: float

@dataclass
class CharacterRef:
    identifier: str
    visual_description: str
    reference_images: list[Path]
    method: str            # "lora" | "ip_adapter"
    lora_path: Path | None
    ip_adapter_weight: float

@dataclass
class PanelGenerationRequest:
    panel_id: str
    aspect_ratio: float
    pixel_rect: tuple                  # (x, y, w, h)
    bleed_edges: set[str]              # {"top", "left", "right", "bottom"}
    loc_identifier: str | None
    loc_description: str
    loc_reference_images: list[Path]
    characters: list[CharacterRef]
    primary_character: str | None
    char_generation_strategy: str      # "single" | "multi_inpaint"
    inpaint_order: list[str]
    occlusion_order: list[str]
    shot_hint: str
    mood: str | None
    action: str
    dialogue_lines: list[DialogueLine]
    caption_boxes: list[CaptionBox]
    bubble_layout: list[BubbleRegion]
    composition_flags: dict
    panel_context: dict
    prompt: str | None
    negative_prompt: str | None

@dataclass
class PageLayout:
    page_index: int
    grid_cols: int
    grid_rows: int
    page_width_px: int
    page_height_px: int
    gutter_px: int
    panels: list[PanelGenerationRequest]
```

### 7.2 core/hardware_profile.py

Dependencies: torch, psutil. Runs once at startup.
Consumed by settings_panel.py, runner.py, lora_manager.py.
See Section 5 for full field list and thresholds.

### 7.3 core/asset_registry.py

Manages per-project assets. Loads from projects/[name]/assets/ on project open.
Validates all CBML character identifiers have asset entries (warns, does not error).

Key methods:
- get_character(identifier) -> CharacterRef
- get_location(identifier) -> LocationAsset
- get_style() -> StyleAsset
- has_lora_for(identifier) -> bool

UI auto-populates asset cards by parsing CBML first, extracting all unique
chars: identifiers and loc: identifiers, creating one card per unique value.

### 7.4 pipeline/enricher.py

Dependencies: core/models.py, core/asset_registry.py, cbml_parser
No ML. Fully testable without GPU.

Geometry:
- Aspect ratio: (col_span / total_cols) / (row_span / total_rows) * (page_h / page_w)
- Pixel rect: computed from slot + gutter + page dimensions
- Bleed edges: slot touching col=1 -> left bleed, col=max_cols -> right bleed, etc.

Character strategy:
- 0 chars -> no character conditioning
- 1 char -> single pass
- 2+ chars -> multi_inpaint strategy
- Primary: regex match of chars: identifiers against shot: string.
  Fallback: character with most dialogue lines.
- Inpaint order: primary first, then by dialogue count descending.
- Occlusion order: parse shot: for "foreground", "behind", "over-the-shoulder".

Shot fallback (if shot: absent):
- 0 chars -> "establishing wide shot"
- 1 char -> "medium shot"
- 2 chars -> "two shot"
- 3+ chars -> "group shot"

Bubble layout:
- 1 char -> full panel region
- 2 chars -> left/right halves
- 3 chars -> thirds
- Stack dialogue bubbles top-to-bottom within each character's region.

Composition flags:
- leave_negative_space: True if bubble count >= 3 or any bubble text > 80 chars
- establishing_panel: True if time/location caption present and first panel on page

Loc resolution:
- No spaces in loc: value -> identifier, look up in asset registry
- Contains spaces -> free-text, pass directly to prompt builder

### 7.5 pipeline/prompt_builder.py

Dependencies: core/models.py
No ML. Fully testable without GPU.

Assembly order:
  [style.prompt_prefix]
  [loc_description]
  [character visual descriptions, comma separated]
  [action text — verbatim from CBML]
  [shot_hint]
  [mood]
  [composition flags as natural language]
  [style.prompt_suffix]

Camera deduplication: if shot_hint present, strip matching camera terms from
action text before concatenation. shot_hint always takes precedence.

Composition flag -> text:
- leave_negative_space -> "open composition, space for text overlay, uncluttered background"
- establishing_panel -> "establishing shot, wide environment"

Negative prompt: style preset base + always append:
"text, speech bubbles, captions, watermark, signature"

Future (parked): optional LLM enhancement pass. Off by default. Not in v1.

### 7.6 pipeline/lora_manager.py

Dependencies: diffusers, kohya_ss / sd-scripts (subprocess)

Training:
- Wraps kohya_ss via subprocess
- Default config: rank 16, ~1500 steps, lr 1e-4
- Training phase runs before panel generation begins
- Progress streamed from kohya stdout to UI
- Caveat: kohya_ss setup is complex. Ship pre-configured subprocess call.

Inference:
- Loads .safetensors into diffusers pipeline
- Multiple simultaneous LoRAs via load_lora_weights() + set_adapters()
- Default weights: style 0.8, character 0.6. Exposed as sliders.

Caveat — FLUX LoRA maturity:
FLUX LoRA tooling less mature than SDXL as of early 2026. SDXL is more stable
for LoRA workflows. Expect possible compatibility issues with FLUX + LoRA.

### 7.7 pipeline/ip_adapter_manager.py

Dependencies: diffusers, IP-Adapter weights

- Downloads model on first use, caches locally
- Multiple simultaneous reference images (style + characters)
- IP-Adapter FaceID variant available for better face consistency
- Default weight: 0.6. Exposed as slider.
- Caveat: warn in UI if combined LoRA + IP-Adapter weights exceed ~1.4.

### 7.8 pipeline/controlnet_manager.py

Dependencies: diffusers, ControlNet weights, OpenPose model

ControlNet operates within a single panel generation only.
It is NOT a cross-panel consistency tool.

OpenPose mode (primary generation):
- Maps shot_hint to pose library keypoint file
- Unmapped shot types fall back to no ControlNet
- Start with 5-8 common shot types

Depth mode (inpaint passes):
- Extracts MiDaS depth map from first-pass image
- Conditions subsequent inpaint passes to preserve 3D spatial coherence

### 7.9 pipeline/image_generator.py

Dependencies: diffusers, torch

- Loads base model once, reuses across all panels
- Applies hardware profile flags
- Accepts target resolution from panel aspect ratio + max_resolution setting
- Applies style + character conditioning (LoRA or IP-Adapter)
- Applies ControlNet conditioning if available
- Returns raw PIL Image

Resolution: SDXL native 1024x1024. Rectangular panels via aspect ratio bucketing
(e.g. 2:1 landscape -> 1024x512).

### 7.10 pipeline/inpaint_manager.py

Dependencies: diffusers (inpaint pipeline), segment_anything, torch

Only invoked for multi_inpaint strategy panels.

Per additional character:
1. Run SAM, prompted with character region hint from bubble region assignment
2. Generate binary mask
3. Extract depth map -> ControlNet depth conditioning
4. Run inpaint pass: character LoRA/IP-Adapter + depth ControlNet + mask
5. Composite inpainted region back onto panel image
6. Repeat for next character in inpaint_order

Occlusion ordering: foreground characters inpainted last.

Caveat: most complex and failure-prone pipeline stage. Quality depends on SAM
segmentation accuracy. Expect most iterative refinement post-v1.

### 7.11 pipeline/text_renderer.py

Dependencies: Pillow, MediaPipe

Operates on fully-generated panel image. Produces final sealed panel image.

Step 1 — Face detection (MediaPipe):
Detect face bounding boxes. Store as avoid-regions for bubble placement.

Step 2 — Caption rendering (Pillow):
Fully specified in CBML. Pure pass-through. Render in document order.

Step 3 — Bubble placement:
Start from enricher BubbleRegion assignments. Shift away from faces.
Shift away from already-placed bubbles. Preserve CBML document order.

Step 4 — Bubble rendering:
  speech  -> rounded_rectangle(), solid border, triangular tail
  thought -> ellipse chain cloud, small circle tail
  shout   -> jagged polygon border, bold text
  whisper -> dashed border rectangle, lighter font

Step 5 — SFX rendering (CBML v1.1):
Boxless large text via Bangers/BadaBoom font.
stroke_width + stroke_fill for outline. Rotation via temp canvas composite.
Attributes from CBML: pos, size, color.
Cairo deferred for per-glyph transforms.

Bubble colour (CBML v1.1):
Optional bg attribute on dialogue lines. Default white. Backward compatible.

### 7.12 pipeline/assembler.py

Dependencies: Pillow

Steps:
1. Create blank page canvas at target resolution
2. For each panel in reading order:
   a. Resize to target pixel rect (hybrid strategy)
   b. Paste at (rect.x, rect.y)
   c. Draw gutter lines on non-bleed edges
3. Draw outer page border
4. Save PNG to projects/[name]/cache/pages/

Hybrid resize strategy:
```python
def fit_panel(image, target_w, target_h, max_stretch=0.05):
    src_ratio = image.width / image.height
    target_ratio = target_w / target_h
    ratio_diff = abs(src_ratio - target_ratio) / target_ratio
    if ratio_diff <= max_stretch:
        return image.resize((target_w, target_h), Image.LANCZOS)
    else:
        return centre_crop_to_fill(image, target_w, target_h)
```

max_stretch default 0.05 (5%). Exposed as slider (0-10%).
Rationale: absorbs diffusion resolution snapping without perceptible distortion.
Beyond 5%, centre-crop is cleaner than visible stretch.

Page size options (must be set before enrichment):
  US Comic (6.625x10.25")  ->  1988x3075 px at 300dpi
  A4 (8.27x11.69")         ->  2481x3507 px at 300dpi
  Manga (5x7.5")           ->  1500x2250 px at 300dpi
  Square digital           ->  2048x2048 px

### 7.13 pipeline/upscaler.py

Dependencies: Pillow, realesrgan (optional)

Applied to full stitched page PNG, not individual panels.

Draft:      Pillow LANCZOS. Near-instant.
Production: Real-ESRGAN realesrgan-x4plus-anime model. ~15s/page.
            Anime model chosen specifically for stylised/illustrated content.
            Downloaded on first use, cached locally.

### 7.14 pipeline/exporter.py

Dependencies: Python standard library only

```python
def export_cbz(page_paths: list[Path], output_path: Path) -> None:
    with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_STORED) as cbz:
        for i, path in enumerate(sorted(page_paths)):
            cbz.write(path, f"page_{i+1:03d}.png")
```

ZIP_STORED: no recompression of already-compressed PNGs.
Output: projects/[name]/output/[comic_title].cbz

### 7.15 pipeline/runner.py

The only module that knows the full pipeline sequence.
Individual stage modules know nothing about each other.

Responsibilities:
- Load CBML, asset registry, hardware profile, settings
- Trigger LoRA training for assets needing it (before generation)
- Run enricher across all panels
- Run prompt builder across all enriched requests
- For each panel:
  - Check cache — skip if panels/panel_id.png exists
  - Run image generator
  - Run inpaint manager (if multi_inpaint)
  - Run text renderer
  - Emit progress event: panel_id, preview image, elapsed, estimated remaining
- Run assembler per page
- Run upscaler per page
- Run exporter
- Emit completion event with output path

Resumability: cache check means interrupted runs skip completed panels.
Enriched JSON and prompts also cached — not repeated on resume.

### 7.16 UI Layer

Framework: Gradio. Six-step flow. Steps 3 and 4 auto-populated from parsed CBML.

Step 1 — Upload CBML
  File upload. On upload: parse, validate, show summary.
  Display: page count, panel count, characters list, locations list, warnings.

Step 2 — Style
  Preset dropdown (Silver Age / Manga / Noir / Watercolour / Custom)
  If Custom: master prompt prefix text field
  Optional: style reference images upload
  If images: method selector (IP-Adapter / LoRA) + weight slider

Step 3 — Characters (one card per unique identifier in CBML)
  Character name (read-only)
  Visual description text field (user writes this)
  Reference images upload
  Method selector + weight slider

Step 4 — Locations (one card per unique identifier in CBML)
  Location name (read-only)
  Reference images upload (optional)

Step 5 — Settings
  Page size selector
  Gutter width (px)
  Stretch tolerance slider (0-10%)
  Generation model (SDXL / FLUX)
  Build mode (Draft / Production / Custom)
  If Custom: per-stage method selectors

Step 6 — BUILD
  Validates all required fields
  Shows hardware profile summary + estimated total time
  BUILD button
  Progress: overall bar + current stage label
  Per-panel preview grid (panels appear as they complete, ~90s each)
  Download CBZ button on completion

---

## 8. Build Modes

| Stage | Draft | Production |
|---|---|---|
| Character method | IP-Adapter | LoRA |
| Style method | Preset prompt | Style LoRA (if images provided) |
| Base model | SDXL | FLUX (if VRAM >= 16GB) else SDXL |
| Diffusion steps | 20 | 40 |
| Prompt enhancement | Text assembly | Text assembly (LLM option parked) |
| Upscaler | Pillow LANCZOS | Real-ESRGAN anime |

Custom mode exposes all of the above as individual selectors.
Hardware profile pre-populates defaults based on available VRAM.

---

## 9. Project File Structure (Runtime)

```
projects/
└── my_comic/
    ├── project.json
    ├── source.cbml
    ├── assets/
    │   ├── style/
    │   │   ├── references/
    │   │   ├── style.safetensors
    │   │   └── style_meta.json
    │   ├── characters/
    │   │   └── NOVA/
    │   │       ├── references/
    │   │       ├── nova.safetensors
    │   │       └── meta.json
    │   └── locations/
    │       └── warehouse_night/
    │           ├── references/
    │           └── meta.json
    ├── cache/
    │   ├── enriched/
    │   ├── prompts/
    │   └── panels/
    └── output/
        ├── pages/
        ├── upscaled/
        └── my_comic.cbz
```

---

## 10. CBML Spec Notes

Current version: 1.0 (released, pip package published)

v1.1 planned additions (fully backward compatible):

SFX line type:
  sfx: KRAKOOM pos:center-right size:large color:#ff4400
  Rendered as boxless large text. No speaker, no tail.

Optional bubble bg attribute:
  NOVA: "text" bg:#ffff99
  Default white. Same hex validation as caption bg attribute.

Bleed detection: derived by enricher from slot geometry. No new CBML syntax.

Loc identifier vs free-text: no spaces -> identifier. Contains spaces -> free-text.

---

## 11. Deferred / Future Extensions

| Item | Notes |
|---|---|
| LLM prompt enhancement | Optional toggle, off by default. Not in v1. |
| Per-glyph SFX styling | Cairo/Skia. Pillow + comic font covers 80% of cases in v1. |
| Tile ControlNet upscaling | Diffusion-based upscale pass. Much slower than Real-ESRGAN. |
| Dynamic consistency LoRA | Incrementally train LoRA on generated panels. Risk: may amplify drift. Needs investigation before implementation. |
| Colour palette feedback | Extract palette from completed panels, inject as prompt conditioning. Pure Python, no training, lower risk than dynamic LoRA. |
| Face-anchored crop | Use face detection to choose crop anchor during panel resize. |
| Manga reading order UI | Parser supports manga=True. Needs UI toggle + RTL CBZ ordering. |
| CMYK PDF export | Requires ICC colour profile handling via Pillow. Ship RGB CBZ first. |

---

## 12. Implementation Order

Phase 1 — Foundation (no GPU required)
  1.  core/models.py
  2.  core/hardware_profile.py
  3.  core/asset_registry.py
  4.  pipeline/enricher.py
  5.  pipeline/prompt_builder.py
  6.  pipeline/assembler.py
  7.  pipeline/text_renderer.py

  Milestone: feed a CBML file through enricher + prompt builder, print all
  generated prompts. Feed placeholder images through assembler, produce a
  page PNG. No GPU needed at any point.

Phase 2 — Generation (GPU required)
  8.  pipeline/image_generator.py   (SDXL first)
  9.  pipeline/ip_adapter_manager.py
  10. pipeline/controlnet_manager.py
  11. pipeline/inpaint_manager.py

  Milestone: generate a single panel end-to-end from a CBML panel block with
  IP-Adapter conditioning and ControlNet pose. No LoRA training yet.

Phase 3 — LoRA & Training
  12. pipeline/lora_manager.py

  Milestone: train a character LoRA from reference images and use it in panel
  generation. Compare quality vs IP-Adapter baseline.

Phase 4 — Post-processing & Export
  13. pipeline/upscaler.py
  14. pipeline/exporter.py
  15. pipeline/runner.py

  Milestone: full end-to-end run producing a .cbz from a .cbml file,
  command-line only.

Phase 5 — UI
  16. ui/components/* + app.py
  17. settings_panel.py (hardware-aware defaults)

  Milestone: complete app usable without touching the terminal.

---

## 13. Package Architecture Decision

The pipeline is kept as a single integrated application. cbml_parser is the
sole external package.

Why cbml_parser is a separate package:
- Genuine independent utility — any tool could consume it
- Clean public API with no pipeline dependencies
- Defined spec implementation with its own release cadence

Why no other component warrants separation:
- The enricher is useless without models.py
- The prompt builder is useless without the enricher's output
- The inpaint manager is useless without the image generator
- These components are deeply coupled by design — that coupling IS the pipeline

Splitting them into packages would add versioning, dependency declarations,
install steps and import path complexity around components never used independently.

How complexity is managed instead:
1. models.py as strict contract layer — all inter-module communication via dataclasses
2. runner.py as the sole module that knows the full sequence
3. Cache layer as natural seam — each stage independently runnable and inspectable
4. Individual modules fully testable in isolation via fixture files

Future exception:
text_renderer.py has the weakest pipeline dependencies and could be extracted
if it proves broadly useful outside this project. A v2 decision to be made with
real usage data, not a speculative v1 architecture choice.

---

## 14. Phase 3 — Generation Quality & Polish

**Status:** Planned. End-to-end test on 2026-05-24 surfaced six classes of
issue (see `Current_status_of_implementation_plan.md` § "Phase 2 Complete
but…"). A first wave of low-risk patches has already landed
([§ 14.0](#140-pre-phase-3-patches-landed-2026-05-24)). Phase 3 tackles the
remaining structural fixes.

**Priority order** (run top to bottom; later items often depend on earlier ones):

1. § 14.1 Wan2GP aspect-ratio bug — quick investigation, eliminates letterboxing
2. § 14.2 Comic/anime-tuned face detector — unblocks 14.3 and 14.4
3. § 14.3 Single-pass multi-character when LoRA-free
4. § 14.4 Character-shaped inpaint masks + face-based speaker assignment (coupled)
5. § 14.5 Bubble non-overlap pass + reading-order indicators
6. § 14.6 LoRA training pipeline (`ai_toolkit_bridge.py`)
7. § 14.7 SAM-based segmentation (investment, only if 14.4 falls short)

Each subsection lists ordered steps. Cross-references use `§ N.M` form so this
section can stand alone when handing off.

---

### 14.0 Pre-phase-3 patches landed (2026-05-24)

Context for everything below. These shipped under commit(s) following the e2e
test, before Phase 3 proper began:

| Area | Change |
|---|---|
| Config | `guidance_scale` 5 → 3.5; `inpaint_denoising` 1.0 → 0.65; `seed: null` → 42 (deterministic across panels) |
| ref_preparer | Style refs prepended to supporting list (was appended last; previously buried at `image_refs[3+]`) |
| text_renderer | Dialogue/caption font `W/40` → `W/28`, floor 22 |
| text_renderer | Tail length capped to 25% of panel diagonal (was unbounded) |
| text_renderer | Bubble x anchored to detected face centre (clamped within region); was left-aligned |
| text_renderer | Dialogue rendered in CBML order with per-speaker `y_cursor` state (was grouped by speaker, losing reading flow) |
| text_renderer | Rewired to use the existing **hybrid YuNet + MediaPipe** detector in `face_detector.py` (was inadvertently still on MediaPipe-only — a recovery-commit regression after `git filter-repo`). YuNet catches the medium-distance / multi-character / stylised cases MediaPipe misses; MediaPipe handles the giant-closeup cases YuNet misses. Together they nail most generated panels. |
| ref_preparer | Same rewire — was also still on MediaPipe-only for primary-ref face-aware cropping. |
| text_renderer | Fallback tail target points at region's lower/upper-centre when no face detected (within-region only, not whole-panel) |
| assembler | New `assembly.fit_mode` knob (`contain` default, `cover`, `stretch`); contain letterboxes panels so edge text overlays survive |

---

### 14.1 Wan2GP aspect-ratio bug

**Symptom.** The bridge computes a portrait resolution (e.g. `704x1024`) from
`panel.aspect_ratio` and passes it as `resolution: "WxH"` in the queue task.
Generated panel PNGs come out 848×848 (square) anyway. Downstream the
assembler letterboxes the square panels into portrait slots — correct
behaviour, but ugly white bands.

**Root cause (user-confirmed, 2026-05-24).** Klein/Kontext uses the
`image_start` reference image's aspect as the *output* aspect, overriding
the `resolution` field. This was a known issue and **had a prior fix that
was lost in the git filter-repo / "Recovery from catastrophic failure"
commit** (e0ed274). The fix was to **outpaint or pad the primary
reference image** so its dimensions exactly equal the requested output
resolution, forcing Klein to honour the target aspect.

**Current code state** (the regression).
[`ref_preparer.py:142`](src/lazycomics/ref_preparer.py#L142) calls
`_crop_to_aspect(img, target_aspect)` on the primary ref — this *crops*
the source down to the target aspect (losing content), then
`_resize_long_edge` scales the long edge to `working_resolution` (1024).
That produces an image with the right aspect ratio but **not** the exact
target pixel dimensions Klein needs. The lost fix presumably *padded* or
*outpainted* instead of cropping, producing an image with exact target
dimensions.

**Steps.**

1. **Reconstruct the prior fix.** In `ref_preparer._prepare_one`, replace
   (or supplement) the crop-to-aspect step with a *pad-to-aspect* step:
   compute target `(W, H)` from `panel.aspect_ratio` × `working_resolution`,
   then create a `(W, H)` canvas filled with a neutral colour (or replicate
   edge pixels), and paste the source ref centred inside it. Result: ref
   has *exactly* the resolution Klein will be asked to output.
2. Variant: AI outpaint (using a small img-to-img pass on the ref before
   generation). Higher quality but introduces a dependency. The padded
   version is the cheap fix; outpaint is the polished version.
3. Add a debug-print line in the bridge that compares
   `(image_start_w, image_start_h)` to `resolution` and warns when they
   differ. Catches future regressions immediately.
4. Add a `_collect_outputs` assertion: warn loudly when an output panel's
   dimensions diverge from the requested resolution by > 5%.
5. Add a `ref_preparer` test that asserts the primary ref dimensions
   exactly match the target panel resolution after preparation.
6. Verify on a real run: panels should come out at the requested
   resolution (e.g. 704×1024 for portrait).

**Notes for whoever picks this up.**

* The 848×848 number is suspicious — it's not the `base_resolution: 1024`
  default, nor the source character refs' 600×600 dims, nor any obvious
  Klein default. Investigate whether Wan2GP itself is applying a
  resolution snap (multiples of 16/32/64) that produces 848 from some
  upstream parameter, OR whether Klein has a hardcoded canonical size.
* The previous fix was almost certainly in `ref_preparer.py`, not the
  bridge. The bridge's job is just to pass `image_start` along.
* When the fix lands, § 14.0's `assembly.fit_mode: contain` default
  should be reverted to `cover` — see § 14.9.

**Done when.** Generated panel dimensions match the requested resolution
within 5%; assembled pages show no letterbox bands.

---

### 14.2 Face detection — fill the remaining gap

**Background.** A complete hybrid detector
(`src/lazycomics/face_detector.py`) already exists — YuNet (OpenCV's
lightweight ONNX detector) as primary plus MediaPipe as fallback, with
IoU-based dedup. The bundled ONNX model is at
`src/lazycomics/assets/models/face_detection_yunet_2023mar.onnx`. The
wiring bug fixed in § 14.0 closed the biggest gap (~20% → ~75% recall on
the e2e panels).

**Remaining symptom.** Heavy-linework closeups (e.g. a grizzled face with
strong cross-hatching, panels lit only by warm rim light) still slip past
both detectors. ~1 in 4 panels in the e2e sample.

**Steps** (only if § 14.0 wiring isn't enough in practice):

1. Build a small evaluation harness: take 20+ generated panels, hand-label
   face boxes, measure recall and FP rate for the current hybrid stack.
2. Tune existing knobs first — lower `_YUNET_SCORE_THRESH` from 0.5 to
   0.3, drop `_MEDIAPIPE_MIN_CONF` to 0.3, switch MediaPipe to
   `model_selection=1` (full-range). Re-measure.
3. If still under target, evaluate a third detector specifically tuned
   for anime/manga (`anime-face-detector`, YOLOv5-anime). Add as an
   optional dep and slot it into the strategy stack ahead of YuNet.
4. Persist detected face boxes to `<project>/enriched/<panel_id>.json`
   under a new `detected_faces` field so downstream stages (§ 14.4) can
   consume them without re-detecting on every text render.

**Done when.** Recall on a 20-panel benchmark ≥ 90%; bubble tails point
at faces on essentially every multi-character panel.

---

### 14.3 Single-pass multi-character when LoRA-free

**Symptom.** Current bridge routes any panel with 2+ characters through
`_run_multi_inpaint`, which runs a base pass plus one inpaint per non-primary
character. With rectangular masks and aggressive denoising this produces
"two disjointed images" panels (§ "Phase 2 Complete but…" issue 3).

**Insight.** When no character has a per-character LoRA, the inpaint pass
isn't doing anything the base prompt couldn't do — both characters can be
generated together with a single prompt naming both. Skipping the inpaint
path entirely sidesteps the failure mode.

**Steps.**

1. In `enricher.py`, when computing `char_generation_strategy`, downgrade
   `"multi_inpaint"` to `"single"` if **none** of the characters has a
   `lora` field set in the asset registry. Add an `enricher` config knob
   (`enricher.single_pass_when_lora_free: true` default) so the heuristic
   can be disabled for testing.
2. Update `prompt_builder.py` to emit a multi-character base prompt
   when strategy is `"single"` and `len(chars) > 1`. The prompt should
   name all characters and use their `visual_description` fields.
3. No bridge changes needed — the strategy field already routes single
   panels to the batch path.
4. Add an enricher test asserting the strategy downgrade fires when all
   chars are LoRA-free and stays `"multi_inpaint"` when any char has a LoRA.
5. Run a side-by-side: generate the same multi-char panel with strategy
   forced to `"single"` and `"multi_inpaint"`. Visual review.

**Done when.** Two-character panels with no per-character LoRA render in one
pass without the disjointed-image artifact.

---

### 14.4 Character-shaped masks + face-based speaker assignment

**Coupled.** Both rely on detected face locations on the *generated* panel
(not the predicted `bubble_layout`). § 14.2 must land first.

#### 14.4a Character-shaped inpaint masks

**Symptom.** `_get_character_region` in `wan2gp_bridge.py` builds masks as
full-height vertical stripes. With high denoise this regenerates the entire
slab, including background, producing disjointed panels.

**Steps.**

1. After the base pass in `_run_multi_inpaint`, run face detection on the
   generated panel (using § 14.2's detector).
2. For each detected face, expand the bbox by `mask_padding_px` (config
   knob, default ~80px) horizontally and ~3× vertically to cover chest +
   head + shoulders.
3. Sort faces by x-coordinate; map to characters in `inpaint_order` by
   index (after primary character is removed).
4. For each inpaint pass, generate a mask containing only that character's
   expanded box — not a full-height stripe.
5. If face detection fails for a panel, fall back to the current
   bubble_layout-derived stripe (so we never block on bad detection).
6. Lower `inpaint_denoising` further to 0.5 for this path (mask is now
   tight, so we want to preserve as much of the surrounding base pass as
   possible).
7. Add `wan2gp.mask_padding_px` to `lazycomics_config.yaml` and the bridge
   fallback table.

**Done when.** Multi-character panels show two distinct characters embedded
in a coherent shared background — no visible seam, no two-image artifact.

#### 14.4b Face-based speaker assignment

**Symptom.** `_select_speaker_face` in `text_renderer.py` trusts the
predicted `bubble_layout` regions. When the painted character lands outside
their predicted region (e.g. because the inpaint mask was loose), the wrong
face is paired with their dialogue.

**Steps.**

1. Read `detected_faces` from enriched JSON (written in § 14.2 step 4).
2. If `len(detected_faces) == len(speaker_order)`: sort faces by x-coord
   (Western reading order) and assign 1:1 to speakers in their CBML
   appearance order.
3. If counts mismatch: keep the existing region-based heuristic.
4. Write the per-speaker assigned face to a new `actual_character_regions`
   field in enriched JSON so future stages can consume it.
5. Update `text_renderer._render_dialogue` to prefer `actual_character_regions`
   over predicted `bubble_layout` when available — the TODO comment at the
   top of `_render_dialogue` already names this.

**Done when.** Bubble tails reliably point at the right character in
multi-character panels.

---

### 14.5 Bubble non-overlap pass + reading-order indicators

**Symptom.** With multiple speakers in tightly-packed regions, bubbles can
collide near region boundaries. The CBML-order fix from § 14.0 helps but
doesn't guarantee non-overlap when regions are adjacent.

**Steps.**

1. In `text_renderer._render_dialogue`, maintain a running list of placed
   bubble rectangles across all speakers.
2. After computing each new bubble's `(bubble_x, bubble_y, bubble_w, bubble_h)`,
   test it against the placed list.
3. On collision: shift the new bubble along the stack axis (down for
   `place_top`, up for `place_bottom`) by `bubble_h + 10` until clear.
4. If the shift would push the bubble outside the panel, shrink
   `max_bubble_width` by 15% and re-wrap the text; retry up to twice.
5. After two retries: render the bubble at its first-attempted position
   and log a warning naming the panel.
6. **Reading-order indicators.** When ≥ 3 bubbles exist in the same panel,
   draw a small "1", "2", "3" badge in the bubble's outer-edge corner.
   New config: `text_renderer.numbered_when_3plus: true`.

**Done when.** No visible bubble overlaps in a 20-panel sample; reading
order is unambiguous in dense panels.

---

### 14.6 LoRA training pipeline (carried from Phase 2)

**Symptom.** § 11.7 character drift across panels. The proper fix is
per-character LoRAs trained on the user's reference images.

**Steps.**

1. Add `src/lazycomics/ai_toolkit_bridge.py` mirroring the
   `wan2gp_bridge.py` pattern (Pinokio-managed subprocess, headless CLI).
2. Public API: `train_character_lora(project, character_id, *, config) -> Path`
   returning the path to the trained `.safetensors`.
3. Read `chars/<character_id>/reference_images/` for training data.
4. Build an AI Toolkit config (per AI Toolkit's `config.yaml` spec) with the
   character's name as the trigger word.
5. Shell out to AI Toolkit's training script in the Pinokio env.
6. On success, call `asset_registry.set_lora(project, character_id, lora_path,
   weight, trigger_word)`.
7. Cache: skip training when a LoRA already exists at the expected path
   unless `force=True`.
8. Add a CLI subcommand: `lazycomics train-lora <project> <character_id>`.
9. Add config section `ai_toolkit:` in `lazycomics_config.yaml`
   (paths to AI Toolkit install, Python env, default training params).
10. Document training time expectations (typically 30-90 min per character).

**Done when.** A character with a trained LoRA looks visually consistent
across all panels they appear in. Side-by-side test: same character before
and after LoRA training.

---

### 14.7 SAM-based segmentation (investment workstream)

**Why this is last.** Big payoff but big effort. Only attempt if § 14.4 isn't
enough.

**Steps.**

1. Add SAM (Segment Anything) as an optional dependency. Choose between
   SAM 1 (smaller, faster) and SAM 2 (better quality, larger).
2. After the base pass, run SAM on the generated panel using detected face
   locations as seed points.
3. Use the resulting segmentation masks as inpaint masks (replaces the
   bounded-box approach in § 14.4a — tighter, follows character silhouette).
4. Negative-space analysis: compute connected components in the
   non-character region. Score each component by area and "distance from
   any character face". Use highest-scoring components as bubble placement
   targets — bubbles get placed in genuine empty regions of the art rather
   than over the character or sky.
5. New text_renderer code path: `placement_strategy: "sam_negative_space"`
   with `"bubble_layout"` as fallback.

**Done when.** Bubbles land in artwork dead space; inpaint regions follow
character silhouettes; visible "seam" artifacts vanish entirely.

---

### 14.8 Bubble placement — further issues after 14.0 patches (2026-05-24)

After the § 14.0 patches landed, a second visual inspection surfaced four
*additional* bubble-placement issues to fix in Phase 3. Logged here so they
don't get lost when the patch-list gets long.

#### 14.8a Place-above-when-face-is-high heuristic

**Symptom.** On `page_001` (NOVA splash), bubble was placed *below* her —
above would have made more sense visually. Cause: current logic in
`_render_dialogue` is "bubble opposite side of face" — face high in
region → bubble at bottom. But when the face is in the *top third*,
there's usually enough headroom above for the bubble too, and convention
puts dialogue above the speaker.

**Fix.** Try `place_top=True` first when face is in the top third; fall
back to bottom only if the bubble wouldn't fit in the headroom above.

#### 14.8b Reading-order vs face-position conflict

**Symptom.** On `page_2_panel_1` (NOVA-then-REX dialogue), NOVA speaks
first in CBML but her bubble landed at *bottom-left* and REX's at
*top-right*. Western reading order is top-left → top-right → bottom-left
→ bottom-right, so the reader hits REX's reply before NOVA's setup.

**Fix.** When there are 2+ speakers in a panel, weight the placement
decision toward CBML order: the earlier speaker's bubble should land in a
position that's read first (upper-left for Western, upper-right for
manga). Each speaker's individual face anchoring still applies *within*
the chosen quadrant.

#### 14.8c Face detection still missing some panels

**Symptom.** Even with YuNet + MediaPipe hybrid, some heavily-stylised
closeups (e.g. `page_2_panel_2` — grizzled face with strong line-art) yield
zero detections. Bubble then has no tail.

**Fix.** Already tracked in [§ 14.2](#142-face-detection--fill-the-remaining-gap).
Pull that work forward in priority.

#### 14.8d Tail length cap is still too generous

**Symptom.** On `page_001`, the speech-bubble tail visibly traverses
most of the panel even with the 25%-of-diagonal cap added in § 14.0.

**Fix.** Tighten the cap to **15% of panel diagonal** (was 25%). Also
add a *minimum* — under 12px, just draw a small triangle stub flush
with the bubble rather than a vanishing thin sliver.

---

### 14.9 Open question — letterbox default (raised 2026-05-24)

**Decision needed.** § 14.0 changed `assembly.fit_mode` default from
`cover` (crop overflow) to `contain` (letterbox with bg padding) to stop
edge-aligned text overlays getting clipped on the assembled page. User
flagged that this *wasn't a discussed change* and may regress an earlier
deliberate decision in favour of cover-crop.

**Why the change was made.** With `cover` the assembled page clipped
caption boxes and SFX text that the renderer places at the panel margin
(20px from each edge). With `contain` the panels are letterboxed (white
bands appear when panel aspect ≠ slot aspect).

**Root cause is actually § 14.1.** The choice between cover-crop and
letterbox only matters because Wan2GP is producing 848×848 panels when
the bridge asks for 704×1024. Fix § 14.1 (Wan2GP aspect-ratio bug) and
panel dims will match slot dims — both modes give identical output, and
the letterbox bands disappear.

**Recommended sequence:**

1. Fix § 14.1 first — eliminates the source of the cover-vs-contain
   dilemma.
2. Revisit the default *after* § 14.1: with panels at the requested
   aspect, default back to `cover` (safer when the renderer's
   edge-anchored text happens to land at the very perimeter of a
   resized panel; cover-crop pulls in slightly and hides nothing
   important).
3. Keep the `fit_mode` knob — it's useful for the cases where panels
   *can't* be regenerated (hand-edited overrides, third-party panels).

**Until § 14.1 lands**, the user may want to revert the default to
`cover`. The knob makes that a one-line YAML edit; no code change
needed.

---

### Phase 3 exit criteria

* Generated panels match requested aspect ratio (§ 14.1).
* Face detection recall ≥ 80% on stylised art (§ 14.2).
* Multi-character panels render without the disjointed-image artifact
  (§ 14.3 + § 14.4a).
* Bubble tails point at the correct speaker on ≥ 90% of multi-character
  panels (§ 14.4b).
* No visible bubble overlaps in a 20-panel review (§ 14.5).
* At least one trained character LoRA improves visible consistency in a
  side-by-side comparison (§ 14.6).
* The original six issues from "Phase 2 Complete but…" are addressed or
  explicitly deferred with a documented reason.
