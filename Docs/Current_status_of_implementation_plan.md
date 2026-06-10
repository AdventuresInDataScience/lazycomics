# lazycomics — implementation status & design principles

Companion to `Implementation_plan.md`. The plan was written before CBML
v1.1 shipped and before any code existed. This document captures (a) what's
been built, (b) what's deferred, (c) the points where reality has diverged
from the original plan, (d) the design principles that emerged during
implementation, and (e) the contracts Phase 2 should populate.

---

## Current status (reconciled 2026-06-05)

**This section is the single source of truth.** Everything below it is the
historical record — the original Phase-1/2 notes, your Phase-2/3 issue reports,
and design principles — kept for context and now annotated inline with status
tags. Where anything below disagrees with this section, this section wins.

**Tests:** 344 runnable unit tests passing, 0 real failures. Not run in the
offline harness (these are environment limits, *not* failures): the 4
`test_face_detector` cases that need pytest's `monkeypatch` fixture, and the
`integration` / `wan2gp_live` suites that need the `cbml_parser` git dependency
or a live Wan2GP install.

**Where each originally-reported issue now stands** (detail + design in
`Implementation_plan.md` § 14.x):

| Issue (as originally reported) | Status | Fixed by / notes |
|---|---|---|
| Panel aspect-ratio bug (portrait JSON → landscape output) | **RESOLVED** | § 14.1 — shared `geometry.resolution_for_aspect`; `ref_preparer` sizes the primary ref to the exact target so `image_start` can't drift; bridge tripwire warns on mismatch |
| Wan2GP painting speech bubbles into the art | **RESOLVED** | § 14.0 — bubble/text/SFX terms lead the default negative prompt |
| Text in bubbles/captions too small | **RESOLVED** | Track 1 — config-driven, panel-relative font sizing (`text.*`) |
| Multiple dialogue: wrong reading order / speaker / overlap | **RESOLVED** | Track 2 (items 7/8) — CBML-order placement, face-aware speaker anchoring |
| Bubbles ignore negative space / random placement | **RESOLVED** | Track 2 — busyness-map cost search places bubbles in empty regions |
| Speech-bubble tails far too long | **RESOLVED** | Track 1 (short tails) + Track 2 (face-anchored short stubs) |
| Thought bubbles clip their text | **RESOLVED** | Track 1 — thought/shout interior-footprint containment |
| Text boxes in weird places | **RESOLVED** | Track 2 placement rework |
| e2e produced no upscaled pages / no `.cbz` | **RESOLVED (code)** | Track 1 — exporter bundles `pages_upscaled/`; e2e page-filename fix. (The e2e *test* still needs `cbml_parser` to run, so it can't execute in this harness.) |
| Inpaint "two disjointed halves" | **ADDRESSED — visual confirmation pending** | § 14.4a — grown/rounded/Gaussian-feathered mask + vertical inset + `inpaint_denoising` 0.65→0.25; **and** § 14.3 single-pass-when-LoRA-free sidesteps the inpaint path entirely for LoRA-free panels |
| Character drift across panels | **OPEN (partially mitigated)** | Item 11. § 14.3 removes inpaint-driven drift for LoRA-free panels; Flux-Dev path + profiler added; § 14.11 per-panel composite-reference experiment specced for a branch; LoRA (§ 14.6) is the backup |
| Art-style drift across panels | **OPEN (partially mitigated)** | Item 11 — same levers as character drift |
| Render artifacts (missing/glazed eyes, small errors) | **OPEN (model-level)** | Not addressed by pipeline code; expected to improve with more steps / guidance tuning or a Flux-Dev switch (profiler added to evaluate) |

**Built during Phase 3 so far:** Track 1 mechanical fixes; Track 2 bubble
placement (items 7/8); § 14.1 aspect fix; § 14.4a inpaint-mask softening +
denoise; § 14.3 configurable `enricher.multi_char_strategy` (auto/single/inpaint,
LoRA-aware); Flux-Dev config guidance + an architecture profiler
(`src/lazycomics/profiling.py`, `tools/profile_architectures.py`, 6-panel
fixture); § 14.11 composite-reference design note (not built — branch
experiment).

**Still open / next:** item 11 character+style drift (validate single-pass +
the composite-ref experiment on real hardware, evaluate Flux Dev via the
profiler, LoRA § 14.6 as backup); § 14.2 comic-tuned face detector; § 14.4a
steps 1–3 (face-driven per-character masks); § 14.5 bubble non-overlap polish;
§ 14.6 LoRA training (`ai_toolkit_bridge.py`); § 14.7 SAM segmentation; § 14.10
stay-resident Wan2GP worker. Render artifacts are model-level (not a pipeline
fix).

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

**Tests:** 228 passing. *(Historical figure — see "Current status" at the top
of this doc; the suite is now at 344 runnable tests.)* End-to-end smoke verified by
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

> **[HISTORICAL — original issue reports.]** Current status of each item below
> is in the "Current status" table at the top of this doc. Most are now
> RESOLVED (text/bubble/aspect issues); style + character drift remain OPEN
> (item 11). Kept here verbatim as the record of what was originally observed.

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
KV caching to improve consistency across generations.
Inpainting improvements: duo prompts ("part of a duo", "hugging"), expand bounding boxes, alpha compositing/soft gradients on masks, and global varnish (denoise/smooth).
Per-scene seed reuse
Explicit bubble optimisation layer
Fixed minimum font size
Generate all characters in one pass where possible

---

## Phase 3 — additional findings from full e2e run (2026-05-25)

The 9-page test comic ran end-to-end after the `image_guide`/`image_mask`
inpaint fix and `_loaded_queue_cache` filter (commit pending). Findings
below are on top of the original six "Phase 2 Complete but…" issues.

### Still broken (carry into Phase 3 work)

> **[HISTORICAL — captured 2026-05-25.]** Tags added below reflect the current
> state (see the "Current status" table at the top for the authoritative view).

* **[OPEN — item 11]** **Character drift across panels.** Same character, same ref image,
  same fixed seed — hair colour, face shape, age all visibly change
  between panels. Style refs and character refs are present and
  consistent at registration time. Most likely fixed by § 14.6 (LoRA
  training) — refs alone aren't strong enough conditioning for Klein. Keen to still look at other options to improve this before resortign to Loras (due to the time commitment and gpu resource commitment required by end users. May not be faesible for all. Need strong brainstorm on other ways to strengthen consistency and adherance to prompy. Perhaps full Flux is the only option? With smal panel sizes to save time - leanign heavier on ESRGAN/Real-ESRGAN etc)
* **[OPEN — item 11]** **Art style drift.** Same comment as above for style consistency
  despite a registered style reference image that all character/loc refs
  visually match. Klein's reference-weighting is too soft.
* **[ADDRESSED — § 14.4a + § 14.3; visual confirmation pending]** **Inpaint structural break persists.** Some multi-character panels
  still come out as two visually disjointed halves even with
  `inpaint_denoising: 0.65` and the corrected `image_guide`/`image_mask`
  fields. Maps to § 14.4 (character-shaped masks) — rectangular masks
  produce hard seams in busy panels. *(Now: feathered/rounded/inset masks +
  denoise 0.25; and single-pass-when-LoRA-free avoids the inpaint path.)*
* **[RESOLVED — § 14.0]** **Wan2GP painting speech bubbles INTO the generated image.** New
  observation. Some generated panels include cartoon speech bubbles
  drawn directly into the artwork, before `text_renderer` runs. The
  panel ends up with two bubble layers — Wan2GP's painted-in bubble plus
  ours. Likely cause: comic-style prompt + character dialogue context
  pushes the model toward "comic with bubbles". **Fix path:** add
  explicit "no speech bubbles, no text, no captions" to the negative
  prompt in `prompt_builder` or `wan2gp_bridge`. Cheap; should land in
  § 14.0-style pre-Phase-3 patches.
* **[RESOLVED — § 14.1]** **Panel aspect-ratio bug confirmed.** Audit of `test_e2e_output/e2e/
  e2e/panels/*.png` shows **every panel is landscape ~1.33 ratio**,
  but enriched JSON for the splash panel (`page_1_panel_1.json`)
  declares `aspect_ratio: 0.6666` (portrait, correct). Wan2GP is
  ignoring or transposing our `resolution` field. Already captured as
  § 14.1; this latest run confirms it's still happening and is the
  single biggest visual issue.
* **[RESOLVED (code) — Track 1]** **e2e test fails.** e2e test fails. terminal output truncated but partial log at `pytest_terminal_output.txt` showing failure. In particular, no upscaling evident, and no comilation into cbz *(exporter now bundles `pages_upscaled/`; e2e page-filename fixed. The test itself needs `cbml_parser` to run.)*
* **[RESOLVED — Track 1]** **Font size in captions and speech bubbles is slightly too big.** Idealy set default smaller, but expose font size in config
* **[RESOLVED — Track 2 (items 7/8)]** **Bubble order totaly incorrect.** NO sensible preservation of reading order as presented in cmbl. Bubbles random and incorrectly placed - incohesive to story. Is the negative space being implemented? Ie logical spaces to leave for text, and logical pickup of these areas to place future bubbles? This fix needs brainstorming *(Now: CBML-order placement + busyness-map negative-space search.)*
* **[RESOLVED — Track 1]** **Thought bubbles much too aggressive.** Curly outer shape cuttin gout most of the text - result looks clipped, amateurish and unreadable
* **[RESOLVED — Track 1 + Track 2]** **Speech bubble tails still far too long** Should be very short as per normal comics. Most generated bubbles have very long tails in the test panels. 
* **[OPEN — model-level]** **Some errors in renders** Eg images with subjects eyes missing/glazed over, and other small errors/discrepancies. 


### Stay-resident Wan2GP worker — added as § 14.10

A new Phase 3 item: build a small daemon (~50 LOC) that imports `wgp.py`
once, loads Flux 2 Klein + Qwen3 8B once, and accepts queue tasks over
stdin. Currently every `_run_wangp` call cold-loads ~18 GB of weights
(~25s per load). Worse, every multi-inpaint pass spawns its own
subprocess — base + N inpaint passes per multi-character panel = N+1
full reloads.

**Why this is Phase-3 work, not a "nice to have":** Phase 3 is the
quality-tuning phase. Every § 14.x fix needs to be validated against the
e2e test. On the current comic that's ~10 model loads per run = ~4
minutes of pure overhead per iteration, on top of actual generation
time. Without the worker, each Phase 3 attempt costs ~10 minutes of
real-time waiting. With it, the same iteration is generation-bound and
significantly faster, which directly translates to more attempts per
session and faster convergence on the visual fixes.

Detailed implementation lives in `Implementation_plan.md` § 14.10. Risk
is the unknown — verify Wan2GP's task-running entry point is callable
repeatedly before committing to the work.

### Image-size audit (sanity check)

Quick check of the test output sizes:

* **Pages:** all 10 page PNGs are 533×800 (ratio 0.666 portrait). Matches
  the test's `page_height_px=800` × `us-comic` aspect resolved to 2:3.
  Note the test uses 800 for speed; production default is 3000 — pages
  will be 2000×3000 in real runs.
* **Panels:** wrong shape (see "Panel aspect-ratio bug confirmed" above).
  Long-edge is also inconsistent — some panels are 1184×880 (larger than
  the requested `base_resolution: 1024`), others 784×576 (smaller).
  Suggests Wan2GP / Flux Klein is snapping to its own preferred buckets
  and our `resolution` param is advisory at best.
* **Panel shape variety:** the 9-page CBML covers `preset:splash`,
  `preset:feature-top`, `grid:3x2`, `preset:wide-bottom`, `grid:2x3`,
  `preset:feature-left`, `grid:3x3`, `spread:2 grid:2x2`, and
  `preset:strip-3` — that's good layout variety. The visual "samey"
  feel of the output is the aspect-ratio bug (§ 14.1), not lack of
  CBML diversity. Once § 14.1 lands, expect splash/feature/spread panels
  to look properly different from grid panels.
