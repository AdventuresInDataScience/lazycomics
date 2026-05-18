# lazycomics — implementation status & design principles

Companion to `Implementation_plan.md`. The plan was written before CBML
v1.1 shipped and before any code existed. This document captures (a) what's
been built, (b) what's deferred, (c) the points where reality has diverged
from the original plan, and (d) the design principles that emerged during
implementation.

---

## Phase 1 — pure-Python pipeline (✓ complete)

| # | Module | Public API | Notes |
|---|---|---|---|
| 1 | `models.py` | 12 dataclasses | `Sfx` added for v1.1 |
| 2 | `config.py` | `load_config`, `cfg_get` | YAML; search `./` then `~/` |
| 3 | `project.py` | `create_project`, `load_project` | `base_dir/{name}/` tree |
| 4 | `asset_registry.py` | `register_*`, `get_*`, `set_lora` | Filesystem IS the registry |
| 5 | `enricher.py` | `enrich` | Sfx propagation + per-panel `aspect_ratio` |
| 6 | `prompt_builder.py` | `build_prompts` | Flux-tuned template |
| 7 | `ref_preparer.py` | `prepare_references` | Face-aware crop (MediaPipe) |
| 8 | `text_renderer.py` | `render_text` | Captions / SFX / bubbles |
| 9 | `assembler.py` | `assemble_pages` | Spread split at midline |
| 10 | `exporter.py` | `export_cbz` | `ZIP_STORED` |

**Plus (out of order, from Phase 2):**

| # | Module | Public API | Notes |
|---|---|---|---|
| 14 | `llm_refiner.py` | `refine_prompts_with_llm` | OpenAI-compat HTTP |

**Tests:** 208 passing. End-to-end smoke test verified: `create_project` →
`register_*` → `enrich` → `build_prompts` → `prepare_references` →
(placeholder PNGs in `panels/`) → `render_text` → `assemble_pages` →
`export_cbz` produces a valid `.cbz` of the expected dimensions.

---

## Phase 2 — generation bridges (pending)

| # | Module | Purpose |
|---|---|---|
| 11 | `wan2gp_bridge.py` | Call Wan2GP API → panel PNGs |
| 12 | `ai_toolkit_bridge.py` | Call AI Toolkit → trained LoRAs |
| 13 | `upscaler.py` | Real-ESRGAN on assembled pages |

These are the Pinokio-launched heavy bridges. The Phase 1 pipeline treats
them as black boxes — supply placeholder PNGs in `panels/` and the
downstream steps don't care where the images came from. Plug Wan2GP in
later without touching anything else.

---

## Key changes since CBML v1.1

The plan was written against an earlier CBML version. v1.1 added three
features that materially affect downstream code:

1. **`comic.aspect`** — newly required in CBML headers. The parser
   resolves named presets (`us-comic`, `manga-tankoubon`, etc.) to
   `(W, H)` tuples. The enricher reads this and computes per-panel
   `aspect_ratio` from canvas × span × slot geometry. This drives
   ref-prep cropping and (Phase 2) panel-generation resolution. The
   plan originally deferred aspect handling; with v1.1 it became
   structural and was lifted into the enricher.
2. **`page.span` / `SpreadLayout`** — `PAGE spread:N` consumes N physical
   pages and renders as one wide canvas. The assembler composites onto
   the wide canvas, then splits at vertical midlines into N physical
   page PNGs — the convention `.cbz` readers expect.
3. **`panel.sfx`** — sound effects as a panel field. We added an `Sfx`
   dataclass and `sfx_lines: list[Sfx]` on `PanelGenerationRequest`,
   threaded through the enricher (with the same `pos → position` field
   rename pattern used for `Caption.pos → CaptionBox.position`) and
   consumed by `text_renderer`.

---

## Design principles

These emerged during implementation. They apply across all modules and
should guide Phase 2 work.

### 1. Lean — build for current consumers only

Add a field / function / abstraction only when something actually consumes
it. We repeatedly shipped without `BubbleRegion` reading,
`get_lora_stack`, `list_characters`, and manifest helpers because nothing
read them yet. They land when a consumer arrives, not in anticipation.

### 2. Loud failures, no silent fallbacks

Where CBML authoring intent matters, failure must be visible. MediaPipe
is a hard dep (not optional) so face-aware cropping always works.
Missing source panels in the assembler yield a labelled gray placeholder,
not a blank slot. LLM refinement failures preserve the original prompt
file rather than blanking it.

### 3. Three-tier resolution for behavioural knobs

`function arg → config (load_config + cfg_get) → hardcoded default`.

Used in `llm_refiner` (system prompt, model, URL, api_key) and
`assembler` (page_height_px, gutter_px, bg_color, stretch_tolerance).
Consistent across the package so users learn the pattern once.

### 4. Skip-if-exists by default; `force=True` to overwrite

`ref_preparer`, `text_renderer`, `assembler`, `exporter` all preserve
user edits on re-run. A hand-touched PNG isn't lost when the user re-runs
the pipeline. Re-run is always safe.

### 5. Mock-friendly seams for testable parsing

Modules that depend on `cbml_parser` (`enricher`, `assembler`) factor out
`_X_from_comic(project, comic, ...)` as a testable seam. The public
function lazy-imports the parser; tests pass `SimpleNamespace` mocks to
the seam. No tests require the real parser to be installed.

### 6. Filesystem as source of truth

The asset registry doesn't have an index file — directory contents ARE
the registry. No manifest drift, no index-rebuild commands. The same
applies to enriched JSON, prompts, prepared refs, etc. — files on disk
are the contract between pipeline steps.

### 7. 0-based in code, 1-based in filenames and IDs

`page_index: 0` in JSON, `page_001.png` on disk, `panel_id="page_1_panel_2"`.
Code uses zero-based math (cleaner); humans read 1-based filenames (less
surprising). The translation happens at exactly one boundary in the
enricher and assembler.

### 8. Field renames happen at boundaries, exactly once

Parser's `Caption(text, bg, color, pos)` becomes our
`CaptionBox(text, bg_color, text_color, position)`. Parser's
`Sfx(text, color, pos)` becomes our `Sfx(text, color, position)`. The
rename happens in the enricher (the boundary between parser-shaped data
and lazycomics-shaped data) and nowhere else. Downstream code sees only
our names.

### 9. Lazy-import heavy / optional deps

`cbml_parser` (heavy), `requests` (extras only) are imported inside the
functions that use them. The package imports fine without them; failure
is localised to the function that needs them, with a clear error.
MediaPipe is the exception — it's used by two core modules and is a
hard dep, imported at module top.

### 10. Module-level functions as test seams

Where we'd otherwise mock a method or class, we use a module-level
function that tests monkeypatch directly. Examples:
`llm_refiner._call_llm`, `ref_preparer._detect_face_centre`,
`text_renderer._bubbles_should_go_top`. Simpler than dependency injection
or fixture frameworks; works in plain unittest-style code.

---

## Plan deviations worth knowing

- **llm_refiner shipped early (Phase 2 → during Phase 1).** It's
  self-contained and the prompt-build flow benefits from it. No harm.
- **`bubble_layout` field on `PanelGenerationRequest`** is in the model
  but currently unused. `text_renderer` auto-places bubbles rather than
  reading hand-set layout. Wire it up when a user wants manual control.
- **Per-asset `list_characters` / `validate_assets` / `get_lora_stack`**
  are in the plan but not implemented. Will land when their consumer
  (likely Phase 2's `build_images`) arrives.
- **`stretch_tolerance` default is 0.0**, not the plan's implicit value.
  Always cover-crops unless dims match exactly; protects faces from
  distortion. Users can opt in to stretching small drifts.
- **`page_size` parameter dropped in favour of `page_height_px`.** The
  CBML parser owns aspect-name resolution; we just need a resolution
  knob. Width derives from `comic.aspect`.
