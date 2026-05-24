# lazycomics — implementation status & design principles

Companion to `Implementation_plan.md`. The plan was written before CBML
v1.1 shipped and before any code existed. This document captures (a) what's
been built, (b) what's deferred, (c) the points where reality has diverged
from the original plan, (d) the design principles that emerged during
implementation, and (e) the contracts Phase 2 should populate.

---

## Phase 1 — pure-Python pipeline (✓ complete)

| # | Module | Public API | Notes |
|---|---|---|---|
| 1 | `models.py` | 12 dataclasses | `Sfx` added for v1.1; Phase 2 fields tagged inline |
| 2 | `config.py` | `load_config`, `cfg_get` | YAML; search `./` then `~/` |
| 3 | `project.py` | `create_project`, `load_project` | `base_dir/{name}/` tree |
| 4 | `asset_registry.py` | `register_*`, `get_*`, `set_lora` | Filesystem IS the registry |
| 5 | `enricher.py` | `enrich` | Sfx, aspect, `bubble_layout` per panel |
| 6 | `prompt_builder.py` | `build_prompts` | Flux-tuned template |
| 7 | `ref_preparer.py` | `prepare_references` | Face-aware crop (MediaPipe) |
| 8 | `text_renderer.py` | `render_text` | Per-type shapes, face-aware placement, tails |
| 9 | `assembler.py` | `assemble_pages` | Spread split at midline |
| 10 | `exporter.py` | `export_cbz` | `ZIP_STORED` |

**Plus (out of order, from Phase 2):**

| # | Module | Public API | Notes |
|---|---|---|---|
| 14 | `llm_refiner.py` | `refine_prompts_with_llm` | OpenAI-compat HTTP |

**Repo artifacts:**

* `lazycomics_config.yaml` at repo root — pre-populated with every key
  the codebase reads via `cfg_get`. Values match each module's hardcoded
  fallback, so the file is documentary unless edited. Three tests in
  `test_config.py` prevent rot: existence check, key coverage, and value
  parity against module defaults.

**Tests:** 228 passing. End-to-end smoke verified by
`tests/test_integration.py::test_full_pipeline_two_pages_spread_two_chars`:
`create_project` → `register_*` → real-parser `enrich` → `build_prompts` →
`prepare_references` → (placeholder PNGs in `panels/`) → `render_text` →
`assemble_pages` → `export_cbz` produces a valid `.cbz` of the expected
dimensions.

---

## Phase 2 — generation bridges (in progress)

| # | Module | Purpose | Status |
|---|---|---|---|
| — | enricher updates | Populate strategy/primary/inpaint fields | ✓ done |
| — | ref_preparer updates | primary_character + inpaint_order aware | ✓ done |
| 11 | `wan2gp_bridge.py` | Call Wan2GP CLI → panel PNGs | ✓ done |
| 12 | `upscaler.py` | Real-ESRGAN on assembled pages | ✓ done |
| 13 | `ai_toolkit_bridge.py` | Call AI Toolkit → trained LoRAs | pending |

These are the Pinokio-launched heavy bridges. The Phase 1 pipeline treats
them as black boxes — supply placeholder PNGs in `panels/` and the
downstream steps don't care where the images came from. Plug Wan2GP in
later without touching anything else.

**Enricher updates (done):** The enricher now populates three fields that
were previously declared but empty:

* `char_generation_strategy` — `"single"` for 0–1 chars, `"multi_inpaint"`
  for 2+. Consumed by `wan2gp_bridge` to decide single-pass vs multi-pass.
* `primary_character` — the character generated in the base pass. Resolved
  by: (1) regex match of character identifier as a word boundary in the
  `shot` string, case-insensitive; (2) most dialogue lines; (3) first in
  CBML `chars:` order.
* `inpaint_order` — primary first, remaining characters sorted by dialogue
  count descending (most important gets the cleanest canvas). Stable on
  equal counts (preserves CBML order).

**Remaining deferred fields (kept, flagged for review):**

* `occlusion_order` — populate when shot-hint depth-cue parsing lands.
* `composition_flags` — populate when `prompt_builder` grows to consume them.
* `panel_context` — populate when a consumer arrives.
* `pose_ref` — populate when ControlNet integration lands.
* `lora_stack` — populate when `ai_toolkit_bridge` lands.
* `pixel_rect` — populate when a post-generation stage needs per-panel coords.
* `PageLayout` — kept on contract; populate if a consumer arises, otherwise
  review for deletion once Phase 2 stabilises.

See `Phase 2 hand-off notes` at the bottom of this doc for the specific
contracts text_renderer / assembler will read from Phase 2 output.

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

## text_renderer — per-type shapes, face-aware placement, best-effort tails

The renderer was upgraded from "rounded rectangle + crude top-or-bottom
switch" to a comic-grade overlay layer:

* **Four bubble shapes**, each implemented as Pillow geometry (no extra
  deps, no second pass):
  * `speech` → rounded rectangle + triangular tail toward the speaker
  * `thought` → cloud outline (overlapping puff arcs) + trailing dots
  * `shout` → 12-point starburst polygon, no tail
  * `whisper` → rectangle with dashed border, no tail
* **Per-speaker region placement.** The enricher now populates
  `PanelGenerationRequest.bubble_layout` with a deterministic geometric
  split (N speakers → N equal horizontal slices in CBML appearance order).
  The renderer reads this and places each speaker's bubbles inside their
  own region, so two-character panels don't overlap and don't fight for
  the same corner.
* **Face-aware top-vs-bottom.** Within each speaker's region, MediaPipe
  face detection finds whichever face falls inside the region. Bubbles
  are placed at the *opposite* vertical half (face in bottom → bubbles
  go top, etc.) so they don't cover the speaker's head.
* **Best-effort tails.** A speech tail points at the matched face in the
  speaker's region; a thought "tail" is a chain of small circles along
  the same line. If no face matches the region (off-panel speaker, model
  painted character outside the predicted region), the renderer skips
  the tail rather than guess wrong. Shout and whisper never draw a tail —
  the shape itself carries the emphasis.

**Best-effort caveat — needs Phase 2 truth source.** For multi-character
panels the speaker→face match relies on *predicted* regions, not the
generator's actual output. If Wan2GP places NOVA on the right when
`bubble_layout` predicts left, the tail anchors at whatever face is
inside the "left" region (which might be REX) and is therefore wrong.
This is the single most important Phase 2 hand-off contract — see below.

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
Consistent across the package so users learn the pattern once. The
shipped `lazycomics_config.yaml` documents every key the codebase reads
and matches the hardcoded fallbacks; rot is prevented by config tests.

### 4. Skip-if-exists by default; `force=True` to overwrite

`ref_preparer`, `text_renderer`, `assembler`, `exporter` all preserve
user edits on re-run. A hand-touched PNG isn't lost when the user re-runs
the pipeline. Re-run is always safe.

### 5. Mock-friendly seams for testable parsing

Modules that depend on `cbml_parser` (`enricher`, `assembler`) factor out
`_X_from_comic(project, comic, ...)` as a testable seam. The public
function lazy-imports the parser; per-module tests pass `SimpleNamespace`
mocks to the seam. `tests/test_integration.py` covers the real parser
path so any drift in the parser API surfaces there.

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
`text_renderer._detect_all_faces`. Simpler than dependency injection
or fixture frameworks; works in plain unittest-style code.

---

## Plan deviations worth knowing

- **llm_refiner shipped early (Phase 2 → during Phase 1).** It's
  self-contained and the prompt-build flow benefits from it. No harm.
- **`bubble_layout` is now populated by the enricher.** Deterministic
  geometric split (N speakers → N equal slices, CBML appearance order).
  Consumed by `text_renderer` for per-speaker region placement; will
  also be consumed by Phase 2's `inpaint_manager` as the inpaint mask
  plan. Authors can override via the JSON between stages, but the
  default is no longer empty.
- **Per-asset `list_characters` / `validate_assets` / `get_lora_stack`**
  are in the plan but not implemented. Will land when their consumer
  (likely Phase 2's `build_images`) arrives.
- **`stretch_tolerance` default is 0.0**, not the plan's implicit value.
  Always cover-crops unless dims match exactly; protects faces from
  distortion. Users can opt in to stretching small drifts.
- **`page_size` parameter dropped in favour of `page_height_px`.** The
  CBML parser owns aspect-name resolution; we just need a resolution
  knob. Width derives from `comic.aspect`.
- **Repo structure is flat `src/lazycomics/`**, not the
  `core/` + `pipeline/` + `ui/` subpackages the plan described. The
  dataclasses-as-contract design (plan §13) doesn't actually need the
  subpackages; less import-path ceremony.

---

## Phase 2 hand-off notes

Things to be mindful of when starting Phase 2. Each is a specific
contract that Phase 1 has built around but does not itself populate.

### 1. Character truth source — **`actual_character_regions`** (NEW field)

`text_renderer`'s tail anchoring currently relies on the *predicted*
`bubble_layout` regions matching where Wan2GP actually painted each
character. For single-character panels this is fine (one face = one
speaker, no ambiguity). For multi-character panels with one Wan2GP call
this would break — but the plan §7.10 routes multi-character panels
through `inpaint_manager`, which means by construction we *choose* each
non-primary character's mask.

**Phase 2 contract:** when `inpaint_manager` paints a character at a
known mask region, it should write that region back to the panel's
enriched JSON as a new field — proposed shape:

```json
"actual_character_regions": {
  "NOVA": [120, 340, 280, 540],
  "REX":  [520, 320, 700, 560]
}
```

`text_renderer._render_dialogue` already has a TODO marker at the top of
the function noting this — when present, it should prefer
`actual_character_regions` over `bubble_layout`. The matching logic in
`_select_speaker_face` then becomes "find faces inside the *actual*
region for this speaker", which removes the ambiguity entirely.

For panels with no inpaint passes (single character), the field is absent
and the existing `bubble_layout` + face-detection path handles it.

### 2. `bubble_layout` already serves double duty

The same `bubble_layout` field that `text_renderer` uses now should be
what `inpaint_manager` reads to choose its SAM-prompted masks. They're
the same regions: predicted-where-each-character-goes. The enricher
populates them today; Phase 2 just needs to consume them.

If Phase 2 chooses different actual mask regions (e.g. SAM segments to
something slightly different than the predicted rectangle), it should
write that back via `actual_character_regions` per (1) so the renderer
gets truth.

### 3. `PanelGenerationRequest` field status

**Now populated by the enricher:**
* `primary_character`, `char_generation_strategy`, `inpaint_order` — the
  multi-character strategy selection from plan §7.4. Consumed by
  `wan2gp_bridge` to route single-pass vs multi-inpaint generation.

**Deferred — kept on contract, flagged for review:**
* `pixel_rect` — panel position within the page canvas. Computed today
  by `assembler._render_page_canvas`; not stored on the request.
  Populate if a post-generation stage needs per-panel coords.
* `occlusion_order` — populate when shot-hint depth-cue parsing lands.
* `composition_flags`, `panel_context` — the "leave_negative_space" /
  "establishing_panel" hints from plan §7.4. Populate when prompt
  building grows to consume them.
* `pose_ref`, `lora_stack` — ControlNet/LoRA wiring. Populate when
  their respective bridges land.
* `prompt`, `negative_prompt` — currently cached as `.txt` files by
  `prompt_builder`; the in-dataclass slots are for the bridge if it
  wants to skip the file round-trip per panel.

Any field still deferred when Phase 2 stabilises will be reviewed for
removal.

**Wan2GP bridge (done):** ``wan2gp_bridge.py`` shells out to Wan2GP
headlessly inside the Pinokio-managed Python environment via
``wgp.py --process queue.zip --output-dir``. No Gradio, no server —
just a subprocess into the correct env.

Architecture:

* ``generate_panels(project, panels, force, config)`` — public API,
  follows the same pattern as other modules (skip-if-exists, panels
  filter, force flag).
* ``_build_wangp_task()`` — **sole adapter** between lazycomics and
  Wan2GP. Constructs the settings dict that goes into the queue. If
  field names change between Wan2GP versions, only this function
  needs updating.
* ``_build_queue_zip()`` — packages tasks + embedded ref images into
  the ZIP format Wan2GP's ``--process`` expects (containing ``queue.json``).
* ``_run_wangp()`` — subprocess call with ``cwd`` set to ``wgp_root``,
  using ``python_bin`` from the Pinokio env.
* ``_collect_outputs()`` — positional pairing of Wan2GP output images
  to panel IDs, copies to ``panels/``.

Config (required in ``lazycomics_config.yaml``)::

    wan2gp:
      wgp_root: ~/pinokio/api/wan2gp.git/Wan2GP
      python_bin: ~/pinokio/api/wan2gp.git/env/bin/python
      architecture: flux2_klein_9b
      default_steps: 28
      base_resolution: 1024
      video_prompt_type: KI # auto-derived if omitted (T/K/KI depending on ref count)

Resolution computation: long edge = ``base_resolution``, short edge
derived from the panel's ``aspect_ratio`` and snapped to the nearest
multiple of 64.

### 4. `PageLayout` — kept, review later

`PageLayout` is declared in `models.py`. Nothing constructs or consumes
it. Kept on the contract: populate if a consumer arises (e.g. a shared
page-geometry contract replacing the ad-hoc `_grid_dims` helpers),
otherwise review for deletion once Phase 2 stabilises.

### 5. `text_renderer` runs *after* image generation

Phase 2 must respect the existing pipeline order: `panels/<id>.png`
written by Wan2GP → `panels_text/<id>.png` written by `text_renderer`
→ `pages/page_NNN.png` written by `assembler`. The renderer reads the
generated panel image (for face detection) and the enriched JSON
(for dialogue/captions/sfx/bubble_layout/actual_character_regions). It
does NOT need any Phase 2 module to be importable.

### 6. Heuristic correctness ceiling

Until Phase 2 ships `actual_character_regions`, multi-character tail
anchoring is best-effort. The current document-order matching of
speakers to `bubble_layout` regions catches the common "two characters
facing each other, taking turns" case; it can fail if the generator
flips character positions (NOVA painted on the right when predicted
left). The renderer fails *closed* in that case — no tail rather than
a wrong tail — so the regression is "missing tail", not "tail pointing
at the wrong face". Acceptable for Phase 1 review; flip to truth-source
the moment Phase 2 lands.

### 7. Watch the SAM dep when inpaint lands

`inpaint_manager` is the heaviest Phase 2 module. Plan §7.10 calls for
SAM (Segment Anything) + depth ControlNet + diffusers inpaint pipeline.
The model weights are large (~2.5GB for SAM alone). Lazy-import per
principle #9; do not import at package top.

## Phase 2 Complete but...
### errors noted:
1. The art style is inconsistent. I have a style reference image, and all characters and location reference images match this style too. But for some reason the images drift in style, quite significantly.
2. Ditto the above with characters
3. Ocasionally, a panel generates that looks almost like 2 disjointed images on 1 panel (likely inpaint failure).
4. The text boxes are in wierd places, often with very long tails, and look wrong despite attempts to use a bounding box model to solve 
5. Text in text boxes and bubbles is too small 
6. With multiple bits of dialogue, the boxes often render in the wrong reading order, wrong speaker and/or overlapping. 

### Some ideas I have to causes/solutions: 
FLUX Dev instead of Klein
Lower CFG
Lower inpaint denoise (0.15–0.35)
Smaller masks and specific bubble logic to handle both character order, tail placement, and negative space in the image in a suitable place for future speach bubbles.Eg using rt-detrv2 or SAM.
Stronger style image weighting?
Per-scene seed reuse
Explicit bubble optimisation layer
Fixed minimum font size
Generate all characters in one pass where possible
