# lazycomics — implementation status

Companion to `Implementation_plan.md`. That document holds the full design and
the numbered Phase-3 workstreams (§ 14.x); this one is the **single source of
truth for what is built right now and what comes next**. It is rewritten in
place as work lands — older narrative (issue-by-issue history, superseded
brainstorms, run-by-run audits) lives in git history if it's ever needed.

_Last reconciled: 2026-06-10._

---

## Status at a glance

**Tests:** 351 unit/integration tests passing. The real-generation end-to-end
test (`tests/test_integration_e2e.py`) drives the full pipeline against a live
Wan2GP + Ollama install; it is GPU-bound and host-only, not part of the fast
suite. The `test_wan2gp_live*` suites likewise need a live Wan2GP. LLM
refinement defaults to the local Ollama model named in `lazycomics_config.yaml`
(`llm.model`); the e2e refine stage skips cleanly if that model isn't pulled.

**Phases:**

* **Phase 1 — pure-Python pipeline:** ✓ complete.
* **Phase 2 — generation bridges:** Wan2GP bridge and upscaler done;
  `ai_toolkit_bridge.py` (LoRA) is the one remaining module, deferred behind
  item 11.
* **Phase 3 — quality & polish:** in progress. Mechanical / text / bubble /
  aspect issues resolved (see table). The remaining hard problem is
  **character + art-style drift (item 11)** — the focus of the next push.

### Where each originally-reported issue stands

Detail + design for each fix lives in `Implementation_plan.md` § 14.x.

| Issue | Status | Fixed by / notes |
|---|---|---|
| Panel aspect-ratio bug (portrait JSON → landscape output) | **RESOLVED** | § 14.1 — shared `geometry.resolution_for_aspect`; `ref_preparer` sizes the primary ref to the exact target; bridge tripwire warns on mismatch |
| Wan2GP painting speech bubbles into the art | **RESOLVED** | § 14.0 — bubble/text/SFX terms lead the default negative prompt |
| Text in bubbles/captions too small / too big | **RESOLVED** | Track 1 — config-driven, panel-relative font sizing (`text.*`) |
| Multiple dialogue: wrong reading order / speaker / overlap | **RESOLVED** | Track 2 — CBML-order placement, face-aware speaker anchoring |
| Bubbles ignore negative space / random placement | **RESOLVED** | Track 2 — busyness-map cost search places bubbles in empty regions |
| Speech-bubble tails far too long | **RESOLVED** | Track 1 (short tails) + Track 2 (face-anchored short stubs) |
| Thought bubbles clip their text | **RESOLVED** | Track 1 — thought/shout interior-footprint containment |
| Text boxes in weird places | **RESOLVED** | Track 2 placement rework |
| e2e produced no upscaled pages / no `.cbz` | **RESOLVED** | Track 1 — exporter bundles `pages_upscaled/`; e2e page-filename fix |
| Inpaint "two disjointed halves" | **ADDRESSED — visual confirmation pending** | § 14.4a — grown/rounded/Gaussian-feathered mask + vertical inset + `inpaint_denoising` 0.25; § 14.3 single-pass-when-LoRA-free sidesteps inpaint for LoRA-free panels |
| **Character drift across panels** | **OPEN (item 11)** | § 14.3 removes inpaint-driven drift for LoRA-free panels; Flux-Dev path + profiler added; § 14.11 composite-reference experiment specced; LoRA (§ 14.6) is the backup |
| **Art-style drift across panels** | **OPEN (item 11)** | Same levers as character drift |
| Render artifacts (missing/glazed eyes, small errors) | **OPEN (model-level)** | Not a pipeline fix; expected to improve with more steps / guidance tuning or a Flux-Dev switch |

### Built during Phase 3 so far

Track 1 mechanical fixes; Track 2 bubble placement; § 14.1 aspect fix; § 14.4a
inpaint-mask softening + denoise; § 14.3 configurable
`enricher.multi_char_strategy` (auto/single/inpaint, LoRA-aware); Flux-Dev
config guidance + an architecture profiler (`src/lazycomics/profiling.py`,
`tools/profile_architectures.py`, 6-panel fixture); § 14.11 composite-reference
design note (specced, not built).

### Still open / next

1. **Item 11 — character + style drift** (the headline problem; see "Item 11
   plan" below).
2. § 14.10 stay-resident Wan2GP worker — iteration-speed investment that makes
   every drift experiment affordable.
3. § 14.2 comic-tuned face detector; § 14.4a steps 1–3 (face-driven
   per-character masks); § 14.5 bubble non-overlap polish; § 14.7 SAM
   segmentation.
4. § 14.6 LoRA training (`ai_toolkit_bridge.py`) — backup for item 11.

Render artifacts are model-level, not a pipeline fix.

---

## Module reference

### Phase 1 — pure-Python pipeline (✓ complete)

| Module | Public API | Notes |
|---|---|---|
| `models.py` | 12 dataclasses | `Sfx` added for CBML v1.1; Phase 2 fields tagged inline |
| `config.py` | `load_config`, `cfg_get` | YAML; search `./` then `~/` |
| `project.py` | `create_project`, `load_project` | `base_dir/{name}/` tree |
| `asset_registry.py` | `register_*`, `get_*`, `set_lora` | Filesystem IS the registry |
| `enricher.py` | `enrich` | Sfx, aspect, `bubble_layout`, multi-char strategy per panel |
| `prompt_builder.py` | `build_prompts` | Flux-tuned template; bubble/text negatives |
| `ref_preparer.py` | `prepare_references` | Face-aware crop (MediaPipe) |
| `text_renderer.py` | `render_text` | Per-type shapes, face-aware placement, tails |
| `assembler.py` | `assemble_pages` | Spread split at midline |
| `exporter.py` | `export_cbz` | `ZIP_STORED`; bundles `pages_upscaled/` |
| `geometry.py` | `resolution_for_aspect`, … | Shared aspect/resolution math |
| `llm_refiner.py` | `refine_prompts_with_llm` | OpenAI-compatible HTTP (shipped early) |

`lazycomics_config.yaml` at the repo root is pre-populated with every key the
codebase reads via `cfg_get`; values match each module's hardcoded fallback, so
the file is documentary unless edited. Three tests in `test_config.py` prevent
rot (existence, key coverage, value parity).

### Phase 2 — generation bridges

| Module | Purpose | Status |
|---|---|---|
| `wan2gp_bridge.py` | Call Wan2GP CLI → panel PNGs | ✓ done |
| `upscaler.py` | Real-ESRGAN on assembled pages | ✓ done |
| `ai_toolkit_bridge.py` | Call AI Toolkit → trained LoRAs | pending (item 11 backup) |
| `profiling.py` | Time architectures (Klein vs Flux Dev) | ✓ done (dev utility) |

**Wan2GP bridge.** Shells out headlessly inside the Pinokio-managed Python env
via `wgp.py --process queue.zip --output-dir` — no Gradio, no server.

* `generate_panels(project, panels, force, config)` — public API
  (skip-if-exists, panels filter, force flag).
* `_build_wangp_task()` — **sole adapter** between lazycomics and Wan2GP; if
  field names change between Wan2GP versions, only this function changes.
* `_build_queue_zip()` — packages tasks + embedded ref images into the
  `--process` ZIP (containing `queue.json`).
* `_run_wangp()` — subprocess with `cwd=wgp_root`, using the Pinokio `python_bin`.
* `_collect_outputs()` — positional pairing of output images to panel IDs.

Required config:

```yaml
wan2gp:
  wgp_root: ~/pinokio/api/wan2gp.git/Wan2GP
  python_bin: ~/pinokio/api/wan2gp.git/env/bin/python
  architecture: flux2_klein_9b
  default_steps: 28
  base_resolution: 1024
  inpaint_denoising: 0.25        # § 14.4a
  inpaint_mask_feather: 0.04     # Gaussian feather, fraction of min(w,h)
  video_prompt_type: KI          # auto-derived if omitted (T/K/KI by ref count)
```

Resolution: long edge = `base_resolution`, short edge from the panel's
`aspect_ratio`, snapped to the nearest multiple of 64.

---

## Design principles

These emerged during implementation and guide all further work.

1. **Lean — build for current consumers only.** Add a field/function/abstraction
   only when something consumes it (e.g. `get_lora_stack`, `list_characters`,
   manifest helpers all deferred until a reader arrives).
2. **Loud failures, no silent fallbacks.** MediaPipe is a hard dep so face-aware
   cropping always works; missing source panels yield a labelled gray
   placeholder; LLM-refinement failures (including empty responses) preserve the
   original prompt rather than blanking it.
3. **Three-tier resolution for knobs:** `function arg → config (load_config +
   cfg_get) → hardcoded default`. Consistent across the package.
4. **Skip-if-exists by default; `force=True` to overwrite.** `ref_preparer`,
   `text_renderer`, `assembler`, `exporter` all preserve user edits on re-run.
5. **Mock-friendly seams.** Parser-dependent modules factor out
   `_X_from_comic(...)`; the public function lazy-imports the parser;
   `test_integration.py` covers the real parser path.
6. **Filesystem as source of truth.** No index files — directory contents (and
   the JSON/PNG/txt artifacts between stages) are the contract.
7. **0-based in code, 1-based in filenames/IDs.** `page_index: 0` →
   `page_001.png`, `panel_id="page_1_panel_2"`. Translated at one boundary.
8. **Field renames happen at boundaries, exactly once.** Parser's
   `Caption(...pos)` → our `CaptionBox(...position)`; `Sfx(...pos)` →
   `Sfx(...position)`. In the enricher and nowhere else.
9. **Lazy-import heavy/optional deps** (`cbml_parser`, `requests`, SAM).
   MediaPipe is the exception (core, hard dep, imported at top).
10. **Module-level functions as test seams** (`llm_refiner._call_llm`,
    `ref_preparer._detect_face_centre`, `text_renderer._detect_all_faces`).

---

## Forward contracts (still live)

Things later stages must respect. Phase 1 built around these but does not itself
populate them.

### `actual_character_regions` — the multi-character truth source

`text_renderer`'s tail anchoring currently relies on the *predicted*
`bubble_layout` regions matching where Wan2GP actually painted each character.
Single-character panels are unambiguous (one face = one speaker). For
multi-character panels routed through inpaint, the inpaint stage *chooses* each
non-primary character's mask and should write that region back to the panel's
enriched JSON:

```json
"actual_character_regions": {
  "NOVA": [120, 340, 280, 540],
  "REX":  [520, 320, 700, 560]
}
```

`text_renderer._render_dialogue` has a TODO to prefer this over `bubble_layout`
when present; `_select_speaker_face` then matches faces inside the *actual*
region, removing the ambiguity. Absent (single-character) → existing
`bubble_layout` + face-detection path. Until this lands, multi-character tail
anchoring is best-effort and **fails closed** (no tail rather than a wrong one)
if the generator flips character positions.

### `bubble_layout` serves double duty

The same `bubble_layout` the renderer uses (predicted per-character regions,
CBML appearance order) is what an inpaint stage should read to choose its masks.
If inpaint picks a different region (e.g. SAM segments to something other than
the predicted rectangle), it writes that back via `actual_character_regions`.

### `PanelGenerationRequest` field status

* **Populated by the enricher:** `primary_character`,
  `char_generation_strategy`, `inpaint_order` (multi-character strategy
  selection, § 7.4 / § 14.3); `bubble_layout`; aspect/`aspect_ratio`;
  `sfx_lines`.
* **Deferred — kept on contract, review for removal once Phase 2 stabilises:**
  `pixel_rect` (per-panel page coords; computed in `assembler` today, not
  stored), `occlusion_order` (depth-cue parsing), `composition_flags` /
  `panel_context` (prompt-builder hints), `pose_ref` / `lora_stack`
  (ControlNet/LoRA wiring), in-dataclass `prompt` / `negative_prompt` (cached as
  `.txt` today), and `PageLayout` (declared in `models.py`, nothing
  constructs/consumes it).

### Pipeline order

`panels/<id>.png` (Wan2GP) → `panels_text/<id>.png` (`text_renderer`) →
`pages/page_NNN.png` (`assembler`) → `pages_upscaled/` (`upscaler`) → `.cbz`
(`exporter`). `text_renderer` runs *after* generation and reads the generated
panel (for face detection) + enriched JSON; it needs no Phase 2 module
importable.

### SAM dependency

`inpaint_manager` (§ 14.7) is the heaviest planned module — SAM weights are
~2.5 GB. Lazy-import per principle #9; never at package top.

---

## Item 11 plan — character + art-style drift

The core unsolved problem: same character, same reference image, same fixed
seed, yet hair colour, face shape, age, and overall art style drift visibly
between panels. Style and character references are present and mutually
consistent at registration time — Flux 2 Klein's reference-weighting is simply
too soft to hold identity across independent generations.

**Constraint that shapes the approach:** LoRA training (§ 14.6) would likely fix
it but costs end-users significant time and GPU. We want to exhaust
lighter-weight consistency levers before defaulting to LoRA, so the pipeline
stays feasible for users without training-grade hardware.

**Candidate levers, roughly in order of effort/payoff** — to be validated on
real hardware (the § 14.10 worker makes this affordable):

* **Single-pass-when-LoRA-free (§ 14.3, done).** Removes inpaint-induced drift
  for LoRA-free panels — validate its effect first.
* **Composite reference per panel (§ 14.11, specced).** Build one combined
  reference image (style + each character) so every generation is conditioned on
  the same canonical visuals. Branch experiment.
* **Stronger style-image weighting / lower CFG.** Tune Klein's reference and
  guidance strength.
* **Per-scene seed reuse** and **KV-cache reuse** across generations for
  cross-panel coherence.
* **Flux Dev instead of Klein.** Evaluate via the new profiler; pair with
  smaller panel sizes + heavier Real-ESRGAN upscaling to keep generation time
  manageable.
* **LoRA training (§ 14.6).** The backup if the above can't hold consistency.

Step 3 of the current effort is to plan this properly — pick the evaluation
harness, decide the order of experiments, and define what "consistent enough"
means before spending GPU time.
