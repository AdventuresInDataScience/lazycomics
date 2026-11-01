# lazycomics — plan

_Restarted 2026-11-01, after W0–W4 closed. Their full as-built record
(designs, measurements, decisions, spike data) is in
[`archive/2026-11-01_PLAN_W0-W4.md`](archive/2026-11-01_PLAN_W0-W4.md)._

**How this doc works.** Status lives only in the two tables below:
workstreams, then open problems. When a workstream closes, delete its section
(git history and the archive keep it) and update the tables. Don't annotate
sections with "RESOLVED" notes. A claim only counts as verified when it has
been checked against pixels from a real Wan2GP run, not just unit tests.

| Workstream | Status |
|---|---|
| W0 Housekeeping | **Done** 2026-10-30. |
| W1 Generation contract | **Done, verified** 2026-11-01 by the full e2e. Panels come out at the requested shape, every reference image reaches Klein, and outputs are collected by name as real PNGs. |
| W2 Verification loop | **Done** 2026-10-30: `tools/golden_run.py` (12-panel golden subset, ~10 min) and `tools/contact_sheet.py`. |
| W3 Bubble layout | **Done** 2026-10-30. The real-art check passed 2026-11-01: no reading-order issues on 33 panels. Leftovers moved to W7. |
| W4 Character consistency | **Done** 2026-11-01. Every recurring character is on-model wherever they're drawn: 52/52 on the full story, 30/30 on the 21 panels the fix wasn't tuned on. Optional experiments moved to W8. |
| **W6 LoRA training** | **Next.** Not started. Decisions to settle first are listed in W6. |
| W5 Inpaint path | Dormant until W6 produces a LoRA. Must land before multi-character panels with a LoRA can be judged. |
| W7 Lettering overhaul | Not started. Your 2026-11-01 review found lettering the weakest part of the output. Problems 4, 15, 17, 18, 21–24. No GPU needed. |
| W8 Generation clean-up | Not started. Problems 7, 8, 16, 25, plus golden-run speed. GPU A/B runs. |
| W9 Housekeeping | Not started. Problems 19, 20, plus unused model fields. |

## Where things stand (verified 2026-11-01)

- **Unit tests:** 418 pass (`pytest tests --ignore=tests/e2e`, ~1 min).
- **Full e2e:** passed on real Wan2GP + Ollama.
  - `story.cbml`, 33 panels → pages → `.cbz`, in 41.5 min on AC power: 13 min
    of LLM refinement, 27 min of generation.
  - Output is in `tests/e2e/outputs/e2e/`.
  - Upscaling was skipped: Real-ESRGAN isn't installed (problem 19).
- **Golden reference** for A/B runs: the golden run `w4_prompts`. Its frozen
  prompts are in `tests/e2e/fixtures/golden_prompts/`.
- **Generate on AC power.** On battery the laptop caps the GPU, and large
  panels take 2–2.5× as long. This was old problem 11, closed.

## Open problems

Numbers carry over from the archived plan; code and tools cite them.

| # | Problem | Evidence | Cause / lead | Fix in |
|---|---|---|---|---|
| 4 | Bubble tail points at the wrong character | e2e: `page_5_panel_2` (Gerald and Yuki swapped) and `page_10_panel_1` (David and Ranjeet swapped). `page_5_panel_2` is the worst case: Yuki is on the left and Gerald on the right, so position alone (Yuki listed first) was right, and SFace overruled it. Tails then hand Yuki's lines to Gerald, which reads as the conversation in the wrong order. | Matching blends position with SFace identity (W3). The W3 spike found the blend broke none of 9 panels; this panel shows it can. SFace is weak on this art (cosine 0.1–0.56), so identity can't always overrule position. | W7 |
| 6 | Inpaint settings never reach Wan2GP | Not exercised since May: `auto` sends LoRA-free panels through a single pass. | Inpaint tasks don't send `settings_version`, so wgp forces `denoising_strength = 1.0`. `masking_strength` 0.3 locks the area outside the mask for only ~2 of 4 steps, and flux2 never pastes the original pixels back. Net effect: the whole panel is regenerated. | W5 |
| 7 | Gibberish signage | Bar scenes: neon signs read "PICAK", "CERAID", "DORK". | Location descriptions invite signs, and the negative prompt does nothing unless NAG is on (problem 8). | W8 |
| 8 | Speech bubbles painted into the art; negative prompt inert | Golden subset: 3 of 12 panels at the 2026-10-30 baseline, 2 of 12 in `w4_prompts`. e2e `page_8_panel_2` (your page 9, panel 1): two painted bubbles with non-English text ("!!/", "ちっ!") beside ours. | Klein is guidance-distilled: wgp forces `guidance_scale` to 1, so the negative prompt only acts through NAG, which needs `NAG_scale > 1`. `wan2gp.nag_scale` is now exposed but untested. | W8 |
| 14 | Face detector misses stylised faces and accepts junk | An earlier golden `page_6_panel_1`: Gerald's grinning face missed, a 0.53-score "face" on the floor accepted. | Hybrid YuNet + MediaPipe at threshold 0.5. Tails, attribution and the character-sheet crops all inherit it. | Deferred |
| 15 | Thought-bubble trailing dots are tiny and bunched | Golden `page_1_panel_2`: three dots within ~20px of the cloud. | They share the speech tail's length cap (`tail_frac` × short edge). | W7 |
| 16 | Characters listed on a panel but not drawn | e2e `page_2_panel_1` (wide crowd shot, 4 listed): only Gerald is identifiable. The 4-character 0.22 strip (golden `page_4_panel_1`, story `page_10_panel_1`) drew 2 of 4 in both golden runs, all 4 in the e2e. | Prompt-dependent: crowd and group shots let the generator drop named characters or blend them into the background. | W8 |
| 17 | Captions overlap each other | e2e `page_1_panel_1` (688px wide): the top-left and top-right captions (353 and 333px wide) overlap, and the second hides "…by da". | Each caption is sized and placed at its `pos:` on its own. Bubbles avoid captions; captions don't avoid each other. | W7 |
| 18 | Bubbles cover faces | e2e `page_1_panel_1` (Gerald's thought cloud over his face), `page_2_panel_2` (Yuki's second line over Gerald's head), `page_7_panel_2`. The checks report these as "covers a face"; your review confirmed the first two. | Not investigated: the layout's face-avoidance term didn't hold on these panels. | W7 |
| 19 | The upscale stage has never run | Both e2e runs skipped it. | `realesrgan-ncnn-vulkan` isn't installed or on PATH. | W9 |
| 20 | The Intern has no reference image | Drawn as a different person each time. Identity matching can't place him (e2e `page_4_panel_3`: his line has no tail). | `tests/e2e/fixtures/` has `INTERN.txt` only. | W9 |
| 21 | Tails missing, or too small to see | Your review: no tails on pages 4 (panel 3), 5 (panel 4) and 7 (panel 4). | Four causes in `text_renderer`: (a) shout and whisper bubbles never get a tail, by design (`page_4_panel_3`'s "BANDAID!"); (b) the tail is only drawn up or down, so a bubble level with its speaker's face gets none (`page_1_panel_1`'s thought cloud, golden `page_1_panel_1`'s "Like 2008?"); (c) the speaker wasn't matched to a face, so the tail is left off on purpose (the Intern, problem 20); (d) when drawn, the tail is a fixed 14px wide and at most 5% of the panel's short edge, which vanishes at page scale (`page_5_panel_4`, `page_7_panel_4`). The e2e assembles pages only 800px tall, against 3000px in production, which makes (d) worse. | W7 |
| 22 | Speech bubbles look like square boxes | Your review: pages 2 (panel 2) and 7 (panel 4). | By design: speech is a rounded rectangle with a 20px corner radius (`_draw_speech`). Comics conventionally use ovals. | W7 (decide) |
| 23 | Conversation flow unclear; tails cross other bubbles | `page_3_panel_1`: Gerald's "I have seen worse crashes." and "Much earlier." stack touching, with Yuki's "Like 2008?" level with the first, so his two lines read as one speech. "Much earlier."'s tail runs up through the bubble above it. | Layout keeps CBML order (W3) but has no minimum stagger between consecutive bubbles, and tails aren't treated as obstacles. | W7 |
| 24 | SFX land on faces | Your page 8, panel 2 (`page_8_panel_3`): "SKREEEE" sits across Gerald's mouth. | The script says `pos:center`; SFX are drawn exactly at their anchor and, unlike bubbles, never avoid faces. In a close-up the centre is the face. | W7 |
| 25 | Actions implied only by SFX or dialogue aren't drawn | `page_7_panel_1`: the SFX is "SCRIBBLE SCRIBBLE", but David isn't writing; the prompt never mentions it. | Neither the prompt builder nor the refiner sees SFX or dialogue: the prompt comes from the action, shot, mood, setting and characters only. | W8 |

**Closed** (details in the archive): 1 panel dimensions · 2 text cut off at
panel edges · 3 bubble reading order · 5 character drift · 9 panels were JPEGs
named `.png` · 10 fragile output pairing · 11 slow generation (power state,
not code) · 12 tiny text in narrow panels · 13 colliding text lines.

**Reviewed 2026-11-01 and left as is** (from your read of the e2e pages):
- **Page 7, panel 4: David holding garlic bread aloft.** The script says
  "David raises a tray of garlic bread triumphantly".
- **Page 9, panel 2: David's "the vampire thing" line.** It's in the script.
  The reveal is page 8's bottom panel: red eyes, fangs and "I HAVE A
  CONDITION!".
- **Page 3, panel 2 (odd eyes) and page 7, panel 2 (gaze off the paper).**
  Generation limits with no planned lever. Action wording such as "reading
  the paper" is the cheapest one, if they recur.

---

## Workstreams (open, in order)

### W6 — LoRA training. NEXT.

W6 was the fallback if W4 missed its consistency bar. W4 met the bar without
LoRAs, so W6 isn't needed for consistency on this story. What a character
LoRA still adds: an identity that doesn't hang on one reference image and a
text description. It's also what sends multi-character panels down the
per-character inpaint path (W5).

**Design** (archived `2026-06-10_Implementation_plan.md` §14.6):
- `src/lazycomics/ai_toolkit_bridge.py`, mirroring `wan2gp_bridge.py`: a
  headless subprocess into the Pinokio-managed env.
- `train_character_lora(project, character_id, *, config) -> Path`. On
  success it calls `asset_registry.set_lora(...)` with the character's name
  as the trigger word. It skips characters that already have a LoRA unless
  `force=True`.
- An `ai_toolkit:` config section (install path, Python env, training
  defaults), mirrored in `lazycomics_config.yaml` (the parity test enforces
  it).
- Already wired downstream:
  - `prompt_builder` puts trigger words first;
  - the bridge copies LoRAs into Wan2GP's `loras/` and sends
    `activated_loras` / `loras_multipliers`.

**Checked 2026-11-01:**
- AI Toolkit is installed through Pinokio at `C:\pinokio\api\ai-toolkit.git`
  (commit `ec58dcd`, 2026-05-18). It has a Flux 2 Klein trainer
  (`extensions_built_in/diffusion_models/flux2/flux2_klein_model.py`).
- Its example configs target 24 GB of VRAM; this laptop's GPU has 16 GB.

**Settle before building:**
1. **Training data.** Each character has one reference image, and a LoRA
   usually trains on 10–30 varied ones. Options:
   - generate a varied set per character with Klein from the reference,
     then hand-pick;
   - supply more art.
2. **Compatibility.** Before a full run, confirm Wan2GP loads an AI Toolkit
   Klein LoRA (file format, key names) using a tiny training run.
3. **VRAM.** Find settings that fit 16 GB (quantisation, rank, resolution).
4. **How to judge it.** With a LoRA, `enricher.multi_char_strategy: auto`
   sends that character's multi-character panels to the inpaint path, whose
   settings don't apply yet (problem 6). Until W5 lands, judge on solo panels
   or set `multi_char_strategy: single`.
5. **Done when** (proposal):
   - one character's LoRA trains in a documented time and is registered with
     `set_lora`;
   - a golden run with it is at least as consistent as `w4_prompts`
     (Reference, "Consistency bar").

### W5 — Inpaint path. Dormant until W6.

No action while `auto` routes LoRA-free panels to a single pass. When W6
produces a LoRA, first fix the task so our settings actually apply (problem
6). Only then judge the June mask rework (rounded + feathered mask). The fix:
- send `"settings_version": 2.58`, otherwise `denoising_strength` is forced
  to 1.0;
- send `"masking_strength": 1`, so the area outside the mask stays locked for
  every step (flux2 has no pixel paste-back);
- note that the output aspect follows the `image_guide` aspect at
  `resolution`'s pixel area.

Validate on a real 2-character panel before building face-driven masks (old
§14.4a steps 1–3).

### W7 — Lettering overhaul

Your manual review of the e2e pages (2026-11-01) found the generated art and
the page layout largely fine, and the lettering the weakest part:
- overlapping text;
- text over heads;
- missing or invisible tails;
- wrong speakers;
- unclear conversation order;
- boxy bubbles;
- SFX on faces.

The checks caught only some of this, so the done-when includes your review.
All of it is re-lettering, so no GPU is needed.

1. **Review setup.**
   - A no-GPU re-letter for any project. For example, a `--reletter` option
     on `tools/contact_sheet.py` could run `render_text` + `assemble_pages`
     before the checks, making the e2e output a 33-panel regression set.
     `golden_run.py --no-generate` only covers the golden subset.
   - Have the e2e assemble pages at the production size (`assembly.*`
     config, 3000px) instead of 800px, so what you review is what ships.
2. **Bubble shapes** (problem 22, needs your decision): oval speech balloons
   instead of rounded rectangles. Thought clouds already fit text inside an
   inner ellipse, so the sizing approach exists.
3. **Tails that read** (problems 21, 23, 15):
   - draw tails sideways too, so a bubble level with its speaker still
     points at them;
   - give shouts a spike toward the speaker and whispers a dashed tail;
   - scale the tail's width and length with the font, so it survives page
     scale;
   - route tails around other bubbles;
   - give thought-bubble dots their own length;
   - keep leaving the tail off when the speaker isn't matched (fail closed).
4. **Placement** (problems 17, 18, 23, 24):
   - captions on the same edge share its width;
   - bubbles, clouds and SFX stay off faces (SFX may move within their
     `pos:` zone);
   - consecutive bubbles get a minimum stagger, so that a speaker's two
     lines with another speaker's line between them don't read as one
     speech.
5. **Attribution** (problem 4). `page_5_panel_2` shows SFace overruling a
   correct positional match. Re-measure the blend on the e2e's 33 panels
   before re-tuning: require a clear identity margin before position is
   overruled, or drop identity. The blend weight was chosen on 5 golden
   panels.

**Done when**, on the e2e output and the golden subset:
- the checks report no overlap, face or order issues;
- the only missing tails belong to characters whose face isn't visible;
- your read of the assembled e2e pages finds none of the faults listed above.

### W8 — Generation clean-up

Each item is a golden A/B run against `w4_prompts` on AC power. Keep a change
only if the consistency bar still holds. Every knob is already a config value
(`golden_run.py --set`).

1. **NAG** (problems 7, 8). Try `--set wan2gp.nag_scale=3`, then 2 and 5.
   Confirm `NAG_scale` reached wgp from the panels' settings (Reference,
   "Verifying a change"). Judge painted-in bubbles and signage. If a value
   wins, make it the default in the code and the config file.
2. **Characters not drawn** (problem 16). Try arrangement cues for group
   shots, in the refiner rules or the prompt template (e.g. "four people
   around a table"). Judge on golden `page_4_panel_1`, and on story
   `page_2_panel_1` via the e2e.
3. **Speed.** The golden run takes 582s against W2's 8-minute target. The
   lever is fewer refs, since each costs about one output image of tokens:
   - `--set refs.characters_first=true --set refs.closeup_location=false`;
   - then `--set refs.max_refs=N`.

   Then decide whether to keep the 8-minute target.
4. **SFX and dialogue as prompt context** (problem 25). Pass them to the
   refiner as context, so it can describe the action they imply ("David
   scribbles notes"), without quoting their text. Quoted text invites
   painted lettering (problem 8). Check on `page_7_panel_1` via the e2e.
5. *Optional:* `--set wan2gp.default_steps=8` for detail (Klein is
   distilled for 4).

### W9 — Housekeeping

1. **Install Real-ESRGAN** (`realesrgan-ncnn-vulkan` on PATH, or set
   `upscaler.executable`), then run the e2e so the upscale stage finally runs
   (problem 19).
2. **The Intern's reference image** (problem 20). Add one to the fixtures,
   or accept that he's text-only. It's story content, so your call.
3. **Unused model fields.** Once W5 and W6 settle what they need, delete
   the `PanelGenerationRequest` fields with no consumer (`pixel_rect`,
   `occlusion_order`, `composition_flags`, `panel_context`, `pose_ref`,
   `lora_stack`, in-dataclass `prompt`/`negative_prompt`) and the unused
   `PageLayout` model.
4. **Refinement speed.** 33 prompts took 13 min (~25s each), with Ollama
   loading gemma4 at a 128k context. Try a smaller context on the Ollama
   server (e.g. `OLLAMA_CONTEXT_LENGTH`).

### Deferred (no consumer, or not worth it yet)

- Stay-resident Wan2GP worker (old §14.10). Reconsider if golden iterations
  stay slow after W8.3.
- SAM segmentation (old §14.7).
- Comic-tuned face detector (old §14.2); evidence it may be needed: problem 14.
- An automated consistency score (CLIP crop-vs-reference similarity), only
  if manual scoring becomes the bottleneck.

---

## Reference

### Consistency bar (from W4)

On the per-character contact sheet (`sheet_characters.png`), a reviewer can
identify each recurring character by hair colour/style, age band and outfit
in ≥90% of crops without reading the dialogue.
- Score per character, over the panels where they're actually drawn. Count
  undrawn characters separately (problem 16).
- Each crop is centred on the face W3 matched to that character. Where
  attribution is wrong (problem 4) the crop shows someone else, so judge
  those panels by eye on `sheet_pages.png`.
- Scripted looks count as on-model (red eyes, fangs, David's reading glasses
  when the shot calls for them).

**What made it pass (W4.2), so keep it for new characters:**
- each character's `.txt` states an age band, hair and outfit that match the
  reference image;
- the refiner (`llm_refiner.DEFAULT_SYSTEM_PROMPT`) keeps those descriptions
  verbatim.

Gerald's original "young pale white male" with no hair colour came out
white-haired in 2 of 7 golden panels.

### Wan2GP v11.61 with Klein (from W1)

Traced in the local install (`C:\pinokio\api\wan.git\app`) and spot-checked
by hand. `wan2gp_bridge` cites this table.

| Field / letter | Real behaviour | Cite |
|---|---|---|
| `image_start` | Discarded unless `"S"` is in `image_prompt_type`, which Klein doesn't allow. | `wgp.py:1003-1023` |
| `video_prompt_type` `I` | Use `image_refs`. | `wgp.py:943-950` |
| `video_prompt_type` `K` | Treat `image_refs[0]` as the "main subject / landscape" ref: output aspect = that image's aspect, resized to `resolution`'s pixel area. | `wgp.py:6557-6563`, `utils.py:505-518` |
| `"I"` without `K` | Output = `resolution` exactly (rounded to 16). Every ref is scaled to the output's pixel area at its *own* aspect. | same |
| refs | No count cap and no per-ref weight. Refs are distinguished only by time-offset position (10, 20, 30…). Each ref costs about one output image's worth of tokens. A ref side < 64px or aspect > 8 raises `ValueError`. | `flux2_adapter.py:69-151` |
| `guidance_scale` | Ignored: forced to 1 because Klein 9B has no `guidance_max_phases`. | `flux_main.py:94,286` |
| `negative_prompt` | Used only through NAG, and only when `NAG_scale > 1`. | `flux_main.py:287-293` |
| `settings_version` | Absent → treated as 0 → legacy migrations run. One of them forces `denoising_strength = 1.0`. Current value is **2.58**. | `flux_handler.py:420-421`, `wgp.py:128` |
| `output_filename` | The file's stem (placeholders allowed); wgp adds its own extension. Collisions get a `(2)` suffix instead of overwriting. | `wgp.py:7037-7041` |
| queue tasks | A task that fails validation is skipped and the run still exits 0. A runtime error makes the run exit 1. | `wgp.py:7490-7597` |
| prompt length | Klein's Qwen3 text encoder pads or truncates every prompt to 512 tokens, so prompt length doesn't change generation cost. | `text_encoder_qwen3.py:13,57` |

**What the bridge sends** for a base pass:
- `video_prompt_type: "I"` and `settings_version: 2.58`;
- every ref in `image_refs`, primary first;
- `output_filename` = the panel ID;
- `NAG_scale` only when `wan2gp.nag_scale > 1`.

Outputs are collected by name and re-encoded as PNG. wgp's settings JSON goes
in the PNG `comment` chunk, as wgp itself does.

### Verifying a change

| Changed | Run | Time |
|---|---|---|
| Text rendering / bubble layout | `python tools/golden_run.py --no-generate --out w4_prompts` (re-letters that run's panels in place) | seconds |
| Anything upstream of generation (bridge, refs, `refs`/`wan2gp` config) | `python tools/golden_run.py --force --out <name> --baseline w4_prompts [--set KEY=VALUE ...]` | ~10 min on AC (582s measured); ~23 min on battery |
| Character descriptions or the refiner | the same with `--refine`, then freeze `tests/e2e/outputs/golden/<name>/project/prompts/*.txt` into `tests/e2e/fixtures/golden_prompts/` if it wins | + ~1 min |
| Before a release / after contract changes | `python tests/e2e/test_wan2gp_live_queue.py` (2-panel smoke test, minutes), then `pytest tests/e2e/test_integration_e2e.py -v -s` (replaces `tests/e2e/outputs/e2e/`) | ~40 min on AC |

`golden_run.py` writes `report.txt` (`review.check_panel`: aspect, text cut
off by the slot, reading order, overlaps, faces covered, tails),
`sheet_pages.png` and `sheet_characters.png`. `tools/contact_sheet.py
<project_dir> [--baseline <project_dir>]` writes the same sheets and checks
for any already-rendered project, e.g. after a full e2e run.
- The checks can't judge tail *correctness* or likeness; the sheets are for
  that.
- Panels are kept between runs, so pass `--force` whenever generation inputs
  change, or stale panels get reviewed.
- The subset, and why each page is in it: `GOLDEN_PAGES` in the tool.

**Where runs live.** The layout is in the README's "Development" section.
Everything is under `tests/e2e/`, and run folders are gitignored:
- the inputs are in `fixtures/`;
- live runs go to `outputs/`: `e2e/`, `golden/<name>/`, `live/`,
  `live_queue/`;
- old runs kept on purpose go to `archived_test_outputs/`, date-prefixed.

`golden_run.py` takes a bare run name for `--out`/`--baseline` (a folder in
`outputs/golden/`), or a path. Run names in the archived plans, by current
location:

| Archived name | Now |
|---|---|
| `golden_w4_prompts/` | golden run `w4_prompts` (current A/B reference) |
| `golden_output/` (2026-10-30 baseline) | `archived_test_outputs/2026-10-30_golden_baseline/` |
| `golden_w3/` (W4 rung 1) | `archived_test_outputs/2026-10-30_golden_w3/` |
| `test_e2e_output/` (June e2e run) | `archived_test_outputs/2026-06-10_e2e/` |
| `tests/fixtures/integration/` | `tests/e2e/fixtures/` |

**Panel settings.** Each generated panel carries the settings wgp actually
ran with, as JSON (`num_inference_steps`, `NAG_scale`, `resolution`, `seed`,
per-panel `generation_time`, …). Read them to confirm a `--set` reached wgp,
or to time panels individually:
- panels generated since 2026-11-01 (real PNGs):
  `json.loads(Image.open(p).text["comment"])`;
- older panels (JPEG bytes named `.png`, e.g. golden run `w4_prompts`):
  `json.loads(Image.open(p).getexif().get_ifd(0x8769)[37510][8:])`. The
  first 8 bytes are the `ASCII\0\0\0` charset tag.

### Design principles

1. **Lean.** Add a field/function only when something consumes it.
2. **Loud failures, no silent fallbacks.** Missing panels get a labelled
   placeholder; LLM failures keep the original prompt; dimension mismatches
   and missing outputs warn.
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

### Pipeline + contracts

`enrich` → `build_prompts` → (`refine_prompts_with_llm`) →
`prepare_references` → `generate_panels` (Wan2GP) → `render_text` →
`assemble_pages` → `upscale_pages` → `export_cbz`.

Directories: `enriched/` → `prompts/` → `refs_prepared/` → `panels/` →
`panels_text/` → `pages/` → `pages_upscaled/` → `output/*.cbz`.

- **`bubble_layout`** (enricher): predicted per-character regions, equal
  horizontal slices in CBML order. The renderer uses it as a left-to-right
  prior; the inpaint path uses it for masks.
- **`detected_faces` / `actual_character_regions` / `detected_faces_for`**
  (written into the enriched JSON by `render_text`): the faces found, which
  character each belongs to, and a hash of the panel image. A re-render of
  the same image reuses them, and hand edits are honoured. A regenerated
  image is detected afresh, and `enrich()` clears them.
- **`char_generation_strategy` / `primary_character` / `inpaint_order`**
  (enricher): route single-pass vs inpaint. `auto` picks single-pass unless a
  character has a LoRA.
- **Projects store absolute asset paths** in `enriched/*.json`. Moving a
  project means re-running `enrich` (golden runs do) or rewriting the old
  path prefix.
