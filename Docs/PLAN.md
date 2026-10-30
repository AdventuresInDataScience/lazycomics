# lazycomics — plan

_Started 2026-10-30. Supersedes everything in [`archive/`](archive/README.md)._

**How this doc works.** The status table directly below is the only place
status lives. When a workstream closes, delete its section (git history keeps
it) and update the table — don't annotate sections with "RESOLVED" notes.
A claim only counts as verified when it has been checked against pixels from a
real Wan2GP run, not just unit tests. The old plan marked things resolved on
unit-test evidence alone, and two of those claims turned out to be wrong.

---

## Where things actually stand (verified 2026-10-30)

**Code health is fine.** 348 unit tests pass (`pytest --ignore` the three live
suites, ~20s). The full pipeline ran end-to-end on real Wan2GP + Ollama on
2026-06-10: 33 panels → 10 pages → `.cbz`, output in `test_e2e_output/`.
Upscale was skipped because `realesrgan-ncnn-vulkan` wasn't on PATH.

**2026-10-30 W1 re-run.** 17 panels (pages 2, 4, 5, 6, 10, covering every slot
aspect in the story) regenerated with the W1 bridge fix. The June run's
LLM-refined prompts and seed 42 were reused, so W1 is the only variable. The
script is not in the repo; W2.1 turns it into the golden-subset tool.

**Output quality is not.** Findings from inspecting the June run, updated with
the W1 re-run:

| # | Problem | Old doc said | What the 2026-06-10 output shows | Root cause |
|---|---|---|---|---|
| 1 | **Panel dimensions** — **FIXED, verified 2026-10-30.** 17/17 re-run panels came out at exactly the requested size (448×1024 where June gave 784×576, 224×1024 where it gave 592×448, 1024×512 where it gave 848×624), all within 1.6% of slot aspect. | RESOLVED | Every panel comes out ~4:3 landscape whatever was requested. A 0.44 portrait slot gets 784×576; a 2:1 slot gets 848×624; a 0.22 slot gets 592×448. The pixel *area* matches the request; the *aspect* doesn't. | **Confirmed in Wan2GP source.** The `"K"` in `video_prompt_type: KI` makes wgp take the output aspect from `image_refs[0]`, which is the style ref (1342×996). `resolution` only sets the pixel budget (`fit_canvas: 0`). The formula reproduces 784×576, 1184×880 and 848×624 exactly. No check compares *output* dims to the request, so this went unnoticed. |
| 2 | **Text cut off / bubbles in odd places** (final pages) — **FIXED with #1, verified 2026-10-30.** Re-run pages have no edge-clipped text. `page_010` panel 3 has its characters back. `page_005`'s 2:1 panels regained bubbles that cropping had removed entirely ("Later that night.", "Constantly."). | RESOLVED | On the un-cropped `panels_text/` images, bubbles are mostly well placed and fully legible. On assembled pages, text is sliced at the panel edges. | Downstream of #1: the assembler's `cover` fit crops a 1.35 panel into a 0.44 slot (keeping ~33% of the width) or a 0.22 slot (~17%). `page_010` panel 3 loses its characters entirely. |
| 3 | **Bubble reading order** | RESOLVED | `page_3_panel_1`: CBML order Yuki→Gerald→Yuki→Gerald is rendered fully reversed (last line top-left, first line bottom-centre). | `_place_bubble` is greedy. The first line takes the cheapest slot, which is just above its speaker's head and therefore *low*. Each later line can only go up. The "earlier is higher" bias can't recover. |
| 4 | **Bubble tail points at wrong speaker** | RESOLVED | `page_8_panel_4`: David's line has its tail on Gerald. | `_resolve_speaker_anchors` sorts *speakers only* left to right and matches them to *all* detected faces sorted by x. Silent characters, background extras, or one false-positive face shift every match by one. |
| 5 | **Character drift** — **much improved on solo panels after W1 (first look, not yet measured).** `page_006`'s three Gerald-solo panels now show one consistent young man with messy brown hair. The `page_002` desk panel shows the same Gerald where June had a white-haired stranger. Formal re-baseline is W4.1. | OPEN | Worst on **solo** panels. Gerald's ref is young with brown hair; solo panels 3-4 and 6-1..3 show a white-haired old man and a clownish face. **Multi-character** panels are often excellent: in `page_8_panel_4` all four characters match their refs. | **Confirmed in Wan2GP source: `image_start` is silently discarded for Klein.** It's kept only if `"S"` is in `image_prompt_type`, which Klein doesn't allow. The primary character's ref goes *only* to `image_start` and is excluded from `image_refs` (`ref_preparer._collect_supporting`). So the most important character on every panel has had **no visual reference**; only the other characters (in `image_refs`) were conditioned. |
| 6 | **Inpaint quirks** ("two disjointed halves") | "Addressed, visual confirmation pending" | Not exercised since May. `enricher.multi_char_strategy: auto` routes every LoRA-free multi-char panel to a single pass, so all 33 panels in the June run were `single`. | **Confirmed in Wan2GP source: our inpaint settings never applied.** Tasks don't send `settings_version`, so `flux_handler.fix_settings` forces `denoising_strength = 1.0`, overriding our 0.25. `masking_strength` 0.3 locks the area outside the mask for only ~2 of 4 steps, and flux2 never pastes the original pixels back. Net effect: the whole panel gets regenerated, so the May "two disjointed halves" came from Wan2GP's settings, not our masks. Dormant until a character has a LoRA. |
| 7 | Gibberish signage ("PICAK", "FIOOUFEK") | — | Neon signs in the bar scenes carry nonsense text. | Location descriptions invite signage, and the negative prompt is inert (#8). |
| 8 | **`guidance_scale` and `negative_prompt` do nothing** | (bubble negatives marked as the fix for bubbles painted into the art) | — | Confirmed in Wan2GP source. Distilled Klein 9B has no `guidance_max_phases`, so `guide_scale` is forced to 1. The negative prompt is only used through NAG, and only when `NAG_scale > 1` (default 1). Whatever stopped bubbles being painted in, it wasn't the negatives. |
| 9 | Panel files are JPEGs named `.png` | — | `panels/*.png` start with `ff d8` (JPEG). | wgp writes `.jpg`; `_collect_outputs` copies the bytes to `<id>.png`. Harmless downstream (later stages save real PNGs) but misleading. |
| 10 | Output→panel pairing is fragile | — | Worked in the June run. | `_collect_outputs` pairs by sorted filename (`<timestamp>_seed42_<prompt>.jpg`). Two tasks finishing in the same second sort by prompt text. A failed task shifts every later panel by one. |
| 11 | **Generation ~2.5× slower since W1** | — | W1 re-run: 1597s for 17 panels (avg 83s) vs ~555s for the same panels in June. Small portrait panels are ~1.7× slower (e.g. 23s → 39s). Large panels with 4–6 refs are 3–4× slower (1024² `page_5_panel_3`: 48s → 183s; `page_2_panel_1`: 71s → 213s). | Every panel now sends one more ref (the primary, previously discarded), and each ref costs about an output image's worth of tokens. Small panels slow down by the expected (n+1)²/n² from attention. Large panels fall off a cliff, consistent with VRAM spill on the 16 GB laptop GPU. Levers: W4.2 (fewer refs per panel), W4.5 (lower `base_resolution` + upscale). |
| 12 | **Text tiny and over-wrapped in narrow panels** | — | Visible now that panels aren't cropped. `page_010` (224px-wide panels) gets 1–3 words per line at the 16px floor; one thought cloud's text overruns its outline. | `text.*_font_ratio` and `max_bubble_width` (42% of W) scale with panel **width**, so tall narrow panels get the smallest text. See W3.5. |

---

## Workstreams (priority order)

### W0 — Housekeeping (small, do first)

- [ ] Gitignore `test_e2e_output/` (like `test_live_output/` already is) and
      `git rm -r --cached` it. 350 files, including a 6 MB `.cbz`, went into
      git in `ee90ece`.
- [ ] Delete `_inspect.py` (one-off ref-size dump), `e2e_run.log` and
      `test_e2e_run.log` (May runs, pre-dating every Phase 3 fix;
      `e2e_run.log` is UTF-16).
- [ ] README: "Phase 2 not yet started" is stale and the `docs/STATUS.md` link
      is dead. Point it at this file and fix the quickstart's step 7 comment
      (`generate_panels` exists).
- [ ] `examples/basic_usage.py` docstring says Phase 2 isn't implemented.

### W1 — Fix the generation contract (dimensions + reference routing)

**Why first.** It's the highest-leverage fix in the repo. Problems 1, 2 and
the solo-panel half of 5 share one root cause: `_build_wangp_task` and
`ref_preparer` assumed Klein reads `image_start`, and it doesn't. The fix is
a few task fields plus deleting code. Until the panel shape is right, every
bubble-placement and consistency judgement on assembled pages is
contaminated by cropping.

**What Wan2GP v11.61 actually does with Klein (`flux2_klein_9b`),** traced in
the local install (`C:\pinokio\api\wan.git\app`) and spot-checked by hand:

| Field / letter | Real behaviour | Cite |
|---|---|---|
| `image_start` | Discarded unless `"S"` is in `image_prompt_type`, which Klein doesn't allow. | `wgp.py:1003-1023` |
| `video_prompt_type` `I` | Use `image_refs`. | `wgp.py:943-950` |
| `video_prompt_type` `K` | Treat `image_refs[0]` as the "main subject / landscape" ref: output aspect = that image's aspect, resized to `resolution`'s pixel area. | `wgp.py:6557-6563`, `utils.py:505-518` |
| `"I"` without `K` | Output = `resolution` exactly (rounded to 16). Every ref is scaled to the output's pixel area at its *own* aspect. | same |
| refs | No count cap and no per-ref weight. Refs are distinguished only by time-offset position (10, 20, 30…). Each ref costs about one output-image's worth of tokens. A ref side < 64px or aspect > 8 raises `ValueError`. | `flux2_adapter.py:69-151` |
| `guidance_scale` | Ignored: forced to 1 because Klein 9B has no `guidance_max_phases`. | `flux_main.py:94,286` |
| `negative_prompt` | Used only through NAG, and only when `NAG_scale > 1`. | `flux_main.py:287-293` |
| `settings_version` | Absent → treated as 0 → legacy migrations run. One of them forces `denoising_strength = 1.0`. Current value is **2.58**. | `flux_handler.py:420-421`, `wgp.py:128` |
| `output_filename` | Literal name (placeholders allowed). Collisions get a `(2)` suffix instead of overwriting. | `wgp.py:7037-7041` |

**Landed 2026-10-30** (problems 1, 2; see the table):
- Base-pass tasks send `video_prompt_type: "I"` and `settings_version: 2.58`.
- Every ref goes in `image_refs`, primary first, and `image_start` is no longer
  sent. The ref order is otherwise unchanged (primary, style, location, other
  characters), so the W1 re-run changed one variable; W4.2 tunes it.
- `ref_preparer` keeps refs at native aspect: crop and `detect_face_centre`
  were deleted.
- `geometry` snaps to 16px, Wan2GP's block size. 64 distorted the 0.22 slots
  by 12%.
- `_collect_outputs` warns on output-size mismatch.
- The e2e test asserts panel aspect.

Remaining:

1. **Deterministic output pairing.** Set `output_filename` to the panel ID
   per task and collect by name, not by sorted position. While there,
   re-encode to real PNG (problems 9, 10).
2. **Config honesty.** Mark `wan2gp.guidance_scale` as ignored for distilled
   Klein in `lazycomics_config.yaml`. Decide whether to expose `NAG_scale`
   (W4.3) so the negative prompt can do something.

**Done when:** both are in and the full e2e test passes on real hardware
(it now asserts aspect).

### W2 — A fast, honest verification loop

The full 33-panel e2e run took ~30 min in June and will take roughly 2.5×
that since W1 (problem 11). Apart from W1's new aspect assertion, nothing in
it checks *quality*. Build:

1. **Golden subset fixture.** 2–3 pages cut from `story.cbml` that cover the
   hard cases: tall-narrow slots (0.22, 0.44), a Gerald-solo panel, a
   2-speaker panel with 4 lines (`page_3_panel_1`), a 4-character panel
   (`page_8_panel_4`). Target: <8 min per iteration.
2. **Contact sheets** (`tools/contact_sheet.py`):
   - *Per page:* the raw panel, the panel with text, and the slot-cropped
     result side by side, so cropping damage is visible at a glance.
   - *Per character:* the reference image next to a crop of that character
     from every panel they appear in. This is the consistency yardstick for
     W4.
3. **Machine-checkable assertions** in the e2e test: panel aspect vs request
   (W1.4), no bubble rect outside the visible slot after cropping, and every
   tail anchor inside a detected face box.

**Done when:** one command regenerates the golden subset and writes both
contact sheets.

### W3 — Bubble layout correctness

Re-assess on post-W1 output first: some "placement" complaints will disappear
once panels stop being cropped.

1. **Reading order by construction (problem 3).** Replace greedy per-line
   placement with two phases. (a) Allocate vertical bands top to bottom in
   CBML line order, with a Z-path across columns when two speakers alternate.
   (b) Position each bubble horizontally inside its band near its speaker,
   using the existing cost terms (busyness, face avoidance, tail length).
   Regression test: `page_3_panel_1`'s four lines must read in CBML order
   (each bubble's top-y ≥ the previous one, or same band and to the right).
2. **Speaker attribution (problem 4).** Match *all* panel characters (from
   `bubble_layout` order), not just speakers, to faces. Drop faces below a
   size threshold relative to the largest face, so background extras don't
   count. When face count ≠ character count, match by nearest predicted
   region centre instead of rank, and omit the tail (fail closed) when the
   match is ambiguous.
3. **Persist what was decided.** Write `detected_faces` and
   `actual_character_regions` back to the enriched JSON (the old forward
   contract) so re-renders are deterministic and inspectable.
4. Then polish: ≥3-bubble numbering (only if still needed), tail-length
   minimum stub, thought-bubble cloud sizing.
5. **Size text from panel area, not width (problem 12).** Base font size and
   `max_bubble_width` on `sqrt(W*H)` (or the panel's on-page scale) so a
   224×1024 panel doesn't get the smallest text in the book. Let bubbles in
   narrow panels use most of the width. Regression check: `page_010`.

**Done when:** in the golden subset, every multi-line panel reads in CBML
order and every tail points at the correct character.

### W4 — Character + style consistency

Start only after W1. W1 may remove most of the solo-panel drift on its own,
so re-baseline first with the W2 per-character contact sheet.

**Definition of "consistent enough"** (fix this before spending GPU time). On
the per-character contact sheet, a reviewer can identify each recurring
character by hair colour/style, age band, and outfit in ≥90% of crops without
reading the panel's dialogue. Add an automated score (CLIP image-embedding
similarity of character crop vs reference) only if manual review becomes the
bottleneck.

Experiment ladder, cheapest first. Run each on the golden subset and keep a
result line per experiment in this section:

1. Re-baseline after W1. The primary character will have a reference for the
   first time, so expect a big jump on solo panels.
2. Reference ordering/count. Klein has no per-ref weight, so order and count
   are the only levers. Try character refs before style/location, cap refs
   per panel (dilution, and each ref costs ~1 image of tokens), and drop the
   location ref on close-ups.
3. Steps and NAG. Klein is distilled for 4 steps; try 6–8. `guidance_scale`
   is inert (problem 8), so try `NAG_scale` 2–5 to make the negative prompt
   bite. That may also fix the gibberish signage.
4. Per-panel composite reference (old §14.11): one model-sheet image holding
   exactly that panel's characters. Branch experiment.
5. Flux Dev vs Klein via `tools/profile_architectures.py`, with smaller panels
   plus heavier upscaling to keep time in check.
6. LoRA (W6) if the above can't hit the bar.

### W5 — Inpaint path (dormant)

No action while `auto` routes LoRA-free panels to a single pass. When W6
produces a LoRA, first fix the task so our settings actually apply (problem
6), and only then judge the June mask rework (rounded + feathered mask):

- send `"settings_version": 2.58`, otherwise `denoising_strength` is forced
  to 1.0;
- send `"masking_strength": 1` so the area outside the mask stays locked for
  every step (flux2 has no pixel paste-back);
- note that the output aspect follows the `image_guide` aspect at
  `resolution`'s pixel area.

Validate on a real 2-character panel before building face-driven masks (old
§14.4a steps 1–3).

### W6 — LoRA training (`ai_toolkit_bridge.py`) — backup for W4

Unchanged from the old §14.6 design: mirror the Wan2GP bridge pattern, train
from registered refs, `set_lora()` on success, skip if a LoRA exists. Only
start if W4's ladder fails the consistency bar.

### Deferred (no consumer or not worth it yet)

- Stay-resident Wan2GP worker (old §14.10). Its payoff was amortising model
  loads across inpaint passes, which no longer run. Reconsider if golden-subset
  iterations stay slow.
- SAM segmentation (old §14.7).
- Comic-tuned face detector (old §14.2). Revisit if W3.2's size filtering
  isn't enough.

---

## Design principles (carried over)

1. **Lean.** Add a field/function only when something consumes it.
2. **Loud failures, no silent fallbacks.** Missing panels get a labelled
   placeholder; LLM failures keep the original prompt. Dimension mismatches
   should warn (W1.1).
3. **Three-tier knobs:** function arg → `lazycomics_config.yaml` (`cfg_get`) →
   hardcoded default. The config file mirrors every default; `test_config.py`
   enforces parity.
4. **Skip-if-exists by default; `force=True` to overwrite.** Re-runs preserve
   hand edits.
5. **Mock-friendly seams.** `_X_from_comic(...)` seams for parser-dependent
   code; module-level functions as monkeypatch points
   (`llm_refiner._call_llm`, `text_renderer._detect_all_faces`, …).
6. **Filesystem is the source of truth.** Stage artifacts on disk
   (enriched JSON, prompts, refs, PNGs) are the contracts; no index files.
7. **0-based in code, 1-based in filenames/IDs** (`page_index: 0` →
   `page_001.png`).
8. **Field renames happen once, at the enricher boundary** (parser `pos` →
   our `position`).
9. **Lazy-import heavy/optional deps** (`cbml_parser`, `requests`). MediaPipe
   is the exception (hard dep).
10. **`_build_wangp_task` is the sole Wan2GP adapter.** Wan2GP field semantics
    live there and nowhere else.

## Pipeline + contracts (reference)

`enrich` → `build_prompts` → (`refine_prompts_with_llm`) →
`prepare_references` → `generate_panels` (Wan2GP) → `render_text` →
`assemble_pages` → `upscale_pages` → `export_cbz`.

Directories: `enriched/` → `prompts/` → `refs_prepared/` → `panels/` →
`panels_text/` → `pages/` → `pages_upscaled/` → `output/*.cbz`.

- **`bubble_layout`** (enricher): predicted per-character regions, equal
  horizontal slices in CBML order. Used by the renderer as a left-to-right
  prior and by the inpaint path for masks.
- **`actual_character_regions`** (not yet written; W3.3): where characters
  actually are on the generated panel. Should take precedence over
  `bubble_layout` in the renderer.
- **`char_generation_strategy` / `primary_character` / `inpaint_order`**
  (enricher): route single-pass vs inpaint; `auto` picks single-pass unless a
  character has a LoRA.
- Deferred `PanelGenerationRequest` fields with no consumer (`pixel_rect`,
  `occlusion_order`, `composition_flags`, `panel_context`, `pose_ref`,
  `lora_stack`, in-dataclass `prompt`/`negative_prompt`) and the unused
  `PageLayout` model: delete them unless a workstream above needs them.
