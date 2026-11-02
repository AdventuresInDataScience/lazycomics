# lazycomics — plan

_Restocked 2026-11-02 to put the e2e faults first and all LoRA work last.
The previous plan, with W6's as-built record, is
[`archive/2026-11-02_PLAN_W5-W6.md`](archive/2026-11-02_PLAN_W5-W6.md);
W0–W4 are in [`archive/2026-11-01_PLAN_W0-W4.md`](archive/2026-11-01_PLAN_W0-W4.md)._

**How this doc works.**
- Status lives only in the two tables below: workstreams, then open
  problems. When a workstream closes, delete its section (git history and
  the archive keep it) and update the tables. Don't annotate sections with
  "RESOLVED" notes.
- A claim only counts as verified once it has been checked against pixels
  from a real Wan2GP run, not just unit tests.
- A workstream counts as done only once its done-when has been checked
  against the code and real output, not on a commit message or a session's
  say-so. W5 was reported done on 2026-11-01 with nothing built.

**Priorities, in order:**
1. Fix the faults the 2026-11-01 e2e run showed (W7 lettering, W8
   generation).
2. Get the last pipeline stage running: upscaling (W9).
3. Sign off a full e2e run, every stage, reviewed by you (milestone).
4. Tidy-up (W10).
5. LoRA work (W11), only after the e2e is signed off, on its own branch.
   The pipeline aims for consistency without LoRAs, to avoid the training
   cost.

| Workstream | Status |
|---|---|
| W0–W4 | **Done** 2026-10-30 to 2026-11-01: the generation contract, the verification loop (`tools/golden_run.py`), bubble layout, and character consistency (52/52 appearances on-model in the e2e). Archived. |
| W5 Inpaint path | **Closed** 2026-11-02 with nothing built. Its fix (problem 6) is in W11. |
| W6 LoRA training pipeline | **Done** 2026-11-02: built and unit-tested; Wan2GP loads AI Toolkit's LoRA format (checked on real pixels). No LoRA trained: that's W11. |
| **W7 Lettering** | **Built 2026-11-02; awaiting your read** of the re-lettered e2e pages (done-when's last bullet). The checks meet the rest of the done-when on the e2e and golden panels, and hold on held-out and synthetic panels. See W7. |
| W8 Generation fixes | **Next.** e2e problems 7, 8, 16, 25. GPU A/B runs. |
| W9 Upscaling | Not started. The pipeline's last stage has never run (problem 19). Pick an upscaler and where it sits in the pipeline, then run it. |
| e2e sign-off (milestone) | Not started. A full e2e run, every stage, meeting W7–W9's done-whens and your review. The gate for W11. |
| W10 Tidy-up | Not started. Unused model fields, refinement and golden-run speed. |
| W11 LoRA path | **Parked** until the e2e sign-off; on its own branch. Train and judge one character's LoRA, then fix the inpaint path (problem 6). |

## Where things stand (2026-11-02)

- **Unit tests:** 452 pass (`pytest tests --ignore=tests/e2e`, ~1 min).
- **Full e2e (2026-11-01):** the pipeline runs end to end on real Wan2GP +
  Ollama, but isn't signed off.
  - `story.cbml`, 33 panels → pages → `.cbz`, in 41.5 min on AC power: 13 min
    of LLM refinement, 27 min of generation. Kept as it was generated, the
    "before" every problem below cites, in
    `tests/e2e/archived_test_outputs/2026-11-01_e2e/`.
  - Characters are on-model in all 52 appearances where they're drawn.
  - Your review found the lettering the weakest part (W7), plus faults in
    the art itself (W8). Upscaling was skipped (problem 19, W9).
  - **Re-lettered by W7 (2026-11-02)** in `tests/e2e/outputs/e2e/`, pages
    at 2000×3000: 30/33 panels clean. The only issues left are four
    speakers with no tail because their face wasn't matched (fail closed).
- **Golden reference** for A/B runs: the golden run `w4_prompts`. Its frozen
  prompts are in `tests/e2e/fixtures/golden_prompts/`.
- **Generate on AC power.** On battery the laptop caps the GPU, and large
  panels take 2–2.5× as long.
- **LoRA pipeline:** built (W6) and unused; LoRA-free panels never touch it.

## Open problems

Numbers carry over from the archived plans; code and tools cite them.

| # | Problem | Evidence | Cause / lead | Fix in |
|---|---|---|---|---|
| 4 | Bubble tail points at the wrong character | Residual after W7 (2 of 30 labelled speakers): e2e `page_10_panel_1` and golden `page_4_panel_1`, the 4-character 0.22 strip. Gerald and Yuki are seen from behind, and David is drawn where the script predicts Ranjeet, so David's tail lands on Ranjeet. | Matching is by predicted position only since W7.2 dropped SFace. Position can't tell a mirrored cast when some of it isn't visible. Fixing it needs an identity signal that works on this art (none known; the colour spike in W7 only exploited this cast's hair and skin). | Deferred (identity) |
| 6 | Inpaint settings never reach Wan2GP | Not exercised since May: `auto` sends LoRA-free panels through a single pass. | Inpaint tasks don't send `settings_version`, so wgp forces `denoising_strength = 1.0`. `masking_strength` 0.3 locks the area outside the mask for only ~2 of 4 steps, and flux2 never pastes the original pixels back. Net effect: the whole panel is regenerated. | W11 |
| 7 | Gibberish signage | Bar scenes: neon signs read "PICAK", "CERAID", "DORK". | Location descriptions invite signs, and the negative prompt does nothing unless NAG is on (problem 8). | W8.1 |
| 8 | Speech bubbles painted into the art; negative prompt inert | Golden subset: 3 of 12 panels at the 2026-10-30 baseline, 2 of 12 in `w4_prompts`. e2e `page_8_panel_2` (your page 9, panel 1): two painted bubbles with non-English text ("!!/", "ちっ!") beside ours. | Klein is guidance-distilled: wgp forces `guidance_scale` to 1, so the negative prompt only acts through NAG, which needs `NAG_scale > 1`. `wan2gp.nag_scale` is exposed but untested. | W8.1 |
| 14 | Face detector misses stylised faces and accepts junk | An earlier golden `page_6_panel_1`: Gerald's grinning face missed, a 0.53-score "face" on the floor accepted. e2e `page_5_panel_1` (three at a bar, no dialogue): no faces found. | Hybrid YuNet + MediaPipe at threshold 0.5. Tails, attribution, face avoidance and the character-sheet crops all inherit it. W7 traced no lettering fault to it. | Deferred |
| 16 | Characters listed on a panel but not drawn | e2e `page_2_panel_1` (wide crowd shot, 4 listed): only Gerald is identifiable. The 4-character 0.22 strip (golden `page_4_panel_1`, story `page_10_panel_1`) drew 2 of 4 in both golden runs, all 4 in the e2e. | Prompt-dependent: crowd and group shots let the generator drop named characters or blend them into the background. | W8.2 |
| 19 | The upscale stage has never run | Both e2e runs skipped it. | `realesrgan-ncnn-vulkan` isn't installed or on PATH. | W9 |
| 25 | Actions implied only by SFX or dialogue aren't drawn | `page_7_panel_1`: the SFX is "SCRIBBLE SCRIBBLE", but David isn't writing; the prompt never mentions it. | Neither the prompt builder nor the refiner sees SFX or dialogue: the prompt comes from the action, shot, mood, setting and characters only. | W8.3 |

**Fixed in W7, awaiting your read** (W7 has the detail): 15 thought dots
tiny and bunched · 17 captions overlapping · 18 bubbles covering faces ·
21 tails missing or too small · 23 unclear conversation order, tails
crossing bubbles · 24 SFX on faces · 26 the checks missing lettering
faults · 27 e2e pages reviewed at 533×800. Once you've read the pages, they
move to Closed.

**Closed** (details in the archives): 1 panel dimensions · 2 text cut off at
panel edges · 3 bubble reading order · 5 character drift · 9 panels were JPEGs
named `.png` · 10 fragile output pairing · 11 slow generation (power state,
not code) · 12 tiny text in narrow panels · 13 colliding text lines.

**Decided 2026-11-02, left as is:**
- **Speech bubbles stay rounded rectangles** (was problem 22: your review
  found them boxy on pages 2 and 7). `_draw_speech`, 20px corner radius.
- **The Intern stays text-only** (was problem 20). With no reference image
  he's drawn as a different person each time, matching can't place him, and
  his lines have no tail (fail closed).

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

### W7 — Lettering. Built 2026-11-02; awaiting your read.

**What's left: your read** of the re-lettered e2e pages,
`tests/e2e/outputs/e2e/pages/page_001.png` … `page_010.png` (2000×3000, as
shipped), against the done-when's last bullet. `sheet_pages.png` beside them
has each panel raw | lettered | as placed. The "before" is
`tests/e2e/archived_test_outputs/2026-11-01_e2e/`. When you're happy,
problems 15, 17, 18, 21, 23, 24, 26 and 27 move to Closed and this section is
archived.

**Done-when, checked 2026-11-02:**
- The checks report no overlap, face, order, tail-size or stagger issues on
  the e2e (at 3000px) or the golden subset: e2e 30/33 clean, golden 11/12.
- The only missing tails are speakers whose face wasn't matched: 4 on the
  e2e (`page_4_panel_3` Gerald and the Intern, `page_7_panel_5` Gerald from
  behind, `page_10_panel_1` Gerald from behind) and 1 on golden
  (`page_4_panel_1`).
- My read of all ten pages found no overlapping text, text over heads,
  invisible tails, SFX on faces, or unclear order. One wrong speaker
  remains: problem 4's residual, `page_10_panel_1`, where David's tail
  lands on Ranjeet.

**The lettering bench** (`tools/lettering_bench.py`, ~75s, no GPU) is the
review loop now. It re-letters three sets and tallies the checks, so a
change is judged beyond the panels it was tuned on. You asked for this on
2026-11-02, so fixes don't overfit the test story (e.g. by relying on this
cast's distinct skin tones).
- *tune*: the e2e and `w4_prompts`, re-lettered in place.
- *holdout*: copies of the three older archived runs (57 panels, different
  art). Checked only after a change, never while making it.
- *synthetic*: seeded random comics run through the real pipeline. They
  vary page layouts, aspects, casts, text within a realistic word budget,
  every bubble type, captions, SFX and face sizes.
- It also scores attribution against hand labels
  (`tests/e2e/fixtures/face_labels.json`, 20 labelled panels from the e2e and
  `w4_prompts`, keyed by image hash).

| Issues (panels) | tune (45) | holdout (57) | synthetic (125) |
|---|---|---|---|
| clean panels, before → after | 16 → 41 | 11 → 29 | 25 → 71 |
| face covered | 8 → 0 | 6 → 1 | 44 → 26 |
| stagger | 5 → 0 | 6 → 0 | 10 → 1 |
| tail too small on the page | 17 → 0 | 8 → 0 | 79 → 4 |
| tail crossing / pointing through a box | 2 → 0 | 5 → 0 | 64 → 46 |
| no tail | 15 → 5 | 16 → 2 | 97 → 30 |
| overlap | 2 → 0 | 0 → 0 | 22 → 22 |

- "Before" is the pre-W7 renderer with W7.1's recording, so the new checks
  can see its output.
- The holdout's other issues (36 `slot`, 26 `aspect`) are the June run's
  wrong-aspect panels: a generation fault, closed as problem 1.
- Synthetic faults cluster in crowded panels: 72 of the 94 overlap, face and
  tail issues are in the 23 panels over 40% covered by faces and text, 3 of
  which have more text than fits (`overflow`).
- Full outputs: `bench_w7_before.txt` / `bench_w7_after.txt` in
  `archived_test_outputs/2026-11-01_e2e/`. The before-file's synthetic
  column predates the word budget; the table's synthetic "before" was rerun
  on the same comics.

**As built**, by step:
1. **Review loop** (problems 26, 27).
   - `contact_sheet.py --reletter` re-letters and re-assembles any project at
     `assembly.*` size, in about 16s for the e2e's 33 panels.
   - The e2e test now assembles at production size, and `panel_slots.json`
     records each panel's page `scale`.
   - Checks (`review`):
     - `no_tail` gives its cause and covers every bubble type;
     - new: `tail_size` (on the page, against its height), `tail_cross`
       (into, or pointing through, another box), `stagger`, SFX in `face`,
       and `overflow`;
     - `face` counts a box covering a quarter of a face, or lying a quarter
       on one.
   - Baseline before any lettering change: e2e 12/33 clean, golden 4/12
     (`checks_w7_baseline.txt` in the archive).
2. **Attribution** (problem 4). Re-measured on 20 hand-labelled panels:
   - SFace blend: 4 speakers wrong.
   - Position only: 2 wrong, 1 unplaced.
   - A margin gate can't separate right from wrong: SFace's wrong swap on
     `page_5_panel_2` gains 0.29, as much as the right fix W3 tuned on.
   - **Identity dropped**: `face_identity` and its model download are gone.
   - A hair/skin colour score was tried and rejected as overfitting.
3. **Placement** (17, 18, 23, 24).
   - Captions on one edge share its width; two at one anchor stack.
   - SFX move within their `pos:` zone off faces and captions.
   - Covering a face is the layout's strongest cost.
   - Within a Z-path band each bubble starts a text line lower than the last.
   - A bubble still on a face or SFX is re-wrapped narrower (×0.7, ×0.5),
     and the narrower layout is kept if it costs less.
   - Thought clouds are drawn inside their box (they overshot by up to a
     sixth).
4. **Tails** (21, 23, 15).
   - Every type gets one: speech triangle, whisper dashed, shout spike,
     thought dots.
   - They leave by whichever side faces the speaker, sideways too.
   - Sized from the font: 0.8× wide, 1.3–2.5× long, thought trails 1.8–3.5×;
     `text.tail_scale` replaces `text.tail_frac`. They stop short of the face.
   - Routed so neither the tail nor its line of sight to the face runs
     through another box; the layout pays for crossings it can't route
     round.
   - A speaker's consecutive same-type lines are linked balloons: the tail
     on the first, a connector to each later one.
   - Unmatched speakers still get no tail (fail closed).

**Left open by W7:**
- problem 4's residual (above);
- one held-out face cover (June `page_2_panel_4`);
- the crowded synthetic panels.

### W8 — Generation fixes

The e2e faults that come from the art itself. Each item is a golden A/B run
against `w4_prompts` on AC power. Keep a change only if the consistency bar
still holds. Every knob is already a config value (`golden_run.py --set`).
The sign-off run re-checks the lettering on whatever W8 regenerates.

1. **NAG** (problems 7, 8). Try `--set wan2gp.nag_scale=3`, then 2 and 5.
   Confirm `NAG_scale` reached wgp from the panels' settings (Reference,
   "Verifying a change"). Judge painted-in bubbles and signage. If a value
   wins, make it the default in the code and the config file.
2. **Characters not drawn** (problem 16). Try arrangement cues for group
   shots, in the refiner rules or the prompt template (e.g. "four people
   around a table"). Judge on golden `page_4_panel_1`, and on story
   `page_2_panel_1` via the e2e.
3. **SFX and dialogue as prompt context** (problem 25). Pass them to the
   refiner as context, so it can describe the action they imply ("David
   scribbles notes"), without quoting their text. Quoted text invites
   painted lettering (problem 8). Check on `page_7_panel_1` via the e2e.
4. *Optional:* `--set wan2gp.default_steps=8` for detail (Klein is
   distilled for 4).

**Done when:** each item has a kept or rejected A/B on record, and the
golden subset shows no painted bubbles or gibberish signs and every listed
character drawn, or you've accepted what's left.

### W9 — Upscaling

The pipeline's last stage, `upscale_pages`, has never run (problem 19):
`realesrgan-ncnn-vulkan` was never installed. Before installing it, settle
which upscaler to use and where it goes. What's known (checked 2026-11-02):
- **The current choice is already light.** `realesrgan-ncnn-vulkan` is a
  standalone C++ binary that runs on the GPU through Vulkan: no Python,
  torch or CUDA, just a download (`upscaler.py`'s docstring). It hasn't been
  updated since 2022, and it isn't pip-installable.
- **Wan2GP's env has no ML upscaler for Klein images,** so reusing the
  Pinokio env doesn't help here. Its image "spatial upsampling" is Lanczos
  ×1.5/×2. Its VAE ×2 upscaler only serves models that declare
  `vae_upsampler` (Qwen, Wan), and FlashVSR is video-only (`wgp.py:818-832`,
  `postprocessing/flashvsr/wgp_bridge.py`).
- **Where it sits matters.** Today it upscales assembled pages, lettering
  included. Pages are already 3000px tall (`assembly.page_height_px`), so
  ×4 gives ~8000×12000px. Upscaling each panel's art *before* `render_text`
  would keep the lettering crisp at full size, at the cost of changing what
  `render_text` and the assembler are given.

1. **Decide the target size** (your call): screen reading (a `.cbz` is
   usually ~2000–3000px tall, which assembly already produces), print at
   300 DPI, or both. If it's screen only, Lanczos at assembly may be enough
   and an ML upscaler becomes optional.
2. **Compare the options** on 3–4 e2e pages or panels: line art, flat
   colour and text quality, time per page, install weight.
   - `realesrgan-ncnn-vulkan` with `realesrgan-x4plus-anime` (the current
     choice; a download).
   - An ONNX model run with `onnxruntime` in lazycomics' own env: e.g.
     Real-ESRGAN anime, or another anime/comic-tuned ×2/×4 model, exported
     to ONNX. Pip-only, no torch. On CPU it's likely minutes per page;
     `onnxruntime-directml` uses the GPU on Windows.
   - Lanczos only, no model, as the baseline.
3. **Decide the position:** pages after assembly (today), or panels before
   lettering.
4. **Install or build the winner,** keeping `upscale_pages`' contract
   (`pages/` → `pages_upscaled/`, or the panel-level equivalent), its config
   section and the parity test in step.

**Done when:** the e2e run's upscale stage runs with the chosen option, and
the choice, its install steps and its time per page are documented here and
in the README.

### Milestone — e2e sign-off

The gate before any LoRA work: the whole pipeline, every stage, working
properly.

1. **Full e2e on AC:** `pytest tests/e2e/test_integration_e2e.py -v -s`,
   then `python tools/contact_sheet.py tests/e2e/outputs/e2e`, then your
   read of every page. W10.2 (refinement speed) would cut the run's 13 min
   of refinement, if you want it first.
2. **Keep it.** Move the signed-off run into
   `tests/e2e/archived_test_outputs/<date>_e2e_signoff/`. If W7 or W8
   changed the golden output, replace `w4_prompts` as the A/B reference
   with a fresh golden run.

**Done when:**
- every stage ran, upscaling included, and the `.cbz` opens in a viewer;
- the checks meet W7's done-when;
- your review finds none of the W7 or W8 faults, or you've moved what's
  left to "left as is".

### W10 — Tidy-up

1. **Unused model fields.** Delete the `PanelGenerationRequest` fields with
   no consumer (`pixel_rect`, `occlusion_order`, `composition_flags`,
   `panel_context`, `pose_ref`, `lora_stack`, in-dataclass
   `prompt`/`negative_prompt`) and the unused `PageLayout` model. W11 needs
   none of them: LoRAs attach per character (`CharacterRef.lora`), and the
   inpaint fix only changes task fields.
2. **Refinement speed.** 33 prompts took 13 min (~25s each), with Ollama
   loading gemma4 at a 128k context. Try a smaller context on the Ollama
   server (e.g. `OLLAMA_CONTEXT_LENGTH`).
3. **Golden-run speed.** It takes 582s against W2's 8-minute target. The
   lever is fewer refs, since each costs about one output image of tokens
   (golden A/B, consistency bar must hold):
   - `--set refs.characters_first=true --set refs.closeup_location=false`;
   - then `--set refs.max_refs=N`.

   Then decide whether to keep the 8-minute target.

### W11 — LoRA path. Parked until the e2e sign-off.

Do it on its own branch, cut from the signed-off state, so the main line
stays LoRA-free. The pipeline aims for consistent characters without LoRAs:
training one costs GPU time for every character, plus a one-off ~34 GB
download of a gated base model. W4 already meets the consistency bar with
reference images and descriptions alone. What a character LoRA would add:
an identity that doesn't hang on one reference image and a text
description, and the per-character inpaint path for multi-character panels
(steps 5–6).

**In place (W6, 2026-11-02; full record in the archive).** The workflow is
in `tools/train_lora.py`'s docstring; AI Toolkit's behaviour is in
Reference, "AI Toolkit with Klein".
- `generate_training_set` has Wan2GP draw the character from its reference
  in 20 shots (`_TRAINING_SHOTS`) into
  `assets/characters/<id>/lora_dataset/`, along with the reference.
  Captions read `<ID>, shot, angle, expression, background`. Hand-pick by
  moving rejects into `lora_dataset/rejected/`.
- `train_character_lora` runs AI Toolkit, then calls `set_lora` with trigger
  word = the ID. An existing LoRA is reattached, not retrained, unless
  `force=True`.
- **Compatibility verified on real pixels.** A Klein 9B LoRA built with AI
  Toolkit's own network code loaded in Wan2GP through the bridge with no key
  or shape errors; ×0 matched no LoRA pixel for pixel, ×1.0 changed the
  output. AI Toolkit's own parser accepts the job file the bridge writes.
- **GERALD's training set** is generated (golden run `w6_gerald`, 20 images
  plus the reference, 8.5 min on AC). All 20 are on-model. Reject
  candidates: `_15` (grey-blue skin), `_13` (a stray mark). The generated
  Gerald is drawn more realistically than the reference, and that is the
  look the LoRA will learn.

**Steps:**
1. **You:** accept the licence on
   <https://huggingface.co/black-forest-labs/FLUX.2-klein-base-9B> (gated;
   approval is automatic). Then give AI Toolkit a token:
   `HF_TOKEN=hf_…` in `C:\pinokio\api\ai-toolkit.git\app\.env` (`run.py`
   loads it), or `hf auth login`. The first run downloads ~34 GB: the
   18 GB transformer plus Qwen3-8B.
2. **You:** hand-pick GERALD's set from
   `tests/e2e/outputs/golden/w6_gerald/project/assets/characters/GERALD/lora_dataset_sheet.png`.
3. **Trial run**, to measure VRAM and time per step and confirm a trained
   LoRA loads: `python tools/train_lora.py train
   tests/e2e/outputs/golden/w6_gerald/project GERALD --set
   ai_toolkit.steps=100`. If it runs out of memory, drop 1024 from
   `ai_toolkit.resolutions` first, then try `layer_offloading: true`.
4. **Full run and judge it.** The same command with `--force` (2000 steps),
   then `golden_run.py --lora GERALD=<path> --set
   enricher.multi_char_strategy=single --baseline w4_prompts`.
   - Judge on solo panels. Under `single` the LoRA applies to the whole
     panel, so other characters in it may pick up Gerald's look; under
     `auto`, multi-character panels go to the inpaint path, which is broken
     until step 5.
   - Still unknown: what a trained LoRA does to identity once moved from the
     base model to the distilled one Wan2GP runs.
   - If the final step overfits, try the kept checkpoints
     (`*_000001500.safetensors`, …) via `--lora`, and the weight via
     `--set ai_toolkit.lora_weight=…`.
5. **Fix the inpaint task** (problem 6), so our settings actually apply:
   - send `"settings_version": 2.58`, otherwise `denoising_strength` is
     forced to 1.0;
   - send `"masking_strength": 1`, so the area outside the mask stays locked
     for every step (flux2 has no pixel paste-back);
   - note that the output aspect follows the `image_guide` aspect at
     `resolution`'s pixel area.
6. **Judge the inpaint path** on a real 2-character panel with a LoRA: first
   the June mask rework (rounded + feathered mask), then, only if needed,
   face-driven masks (old §14.4a steps 1–3).

**Done when:**
- one character's LoRA trains in a documented time and is registered with
  `set_lora`;
- a golden run with it is at least as consistent as `w4_prompts`
  (Reference, "Consistency bar");
- a 2-character panel with that LoRA comes out of the inpaint path without
  the "two disjointed halves" seam.

### Deferred (no consumer, or not worth it yet)

- Stay-resident Wan2GP worker (old §14.10). Reconsider if golden iterations
  stay slow after W10.3.
- SAM segmentation (old §14.7).
- Comic-tuned face detector (old §14.2): problem 14. W7 traced no lettering
  fault to it.
- A face-identity signal that works on comic art, for problem 4's mirrored
  casts. SFace didn't (W7.2). Judge any candidate on the bench's labelled
  panels and on art it wasn't tuned on.
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
| `activated_loras` | Looked up by file name in the model's own folder, `loras/flux2_klein_9b/` for Klein 9B. A missing file fails validation, so the task is skipped. | `wgp.py:833-837,3189-3211`, `flux_handler.py:293-300` |
| LoRA keys | BFL-named keys (`diffusion_model.double_blocks.N.img_attn.qkv.lora_A.weight`, as AI Toolkit saves them) pass through unchanged. mmgp rejects the whole LoRA on any unexpected key or shape mismatch, so "was loaded" in the log means every key matched. | `models/flux/model.py:266-367`, `mmgp/offload.py:1582-1588` |

**What the bridge sends** for a base pass:
- `video_prompt_type: "I"` and `settings_version: 2.58`;
- every ref in `image_refs`, primary first;
- `output_filename` = the panel ID;
- `NAG_scale` only when `wan2gp.nag_scale > 1`.

Outputs are collected by name and re-encoded as PNG. wgp's settings JSON goes
in the PNG `comment` chunk, as wgp itself does.

### AI Toolkit with Klein (from W6)

Traced in the Pinokio install (`C:\pinokio\api\ai-toolkit.git\app`, commit
`ec58dcd`). `ai_toolkit_bridge` cites this table.

| Topic | Behaviour | Cite |
|---|---|---|
| model | `arch: flux2_klein_9b` loads `flux-2-klein-base-9b.safetensors` from `name_or_path` (a Hugging Face repo, or a local folder holding the file). It also loads the text encoder `Qwen/Qwen3-8B` and the VAE `ai-toolkit/flux2_vae`, both from Hugging Face. The base repo is gated; the other two aren't. | `extensions_built_in/diffusion_models/flux2/flux2_klein_model.py`, `flux2_model.py:127-267` |
| saved LoRA | PEFT format with no alpha keys (alpha = rank). Keys are renamed from `transformer.` to `diffusion_model.`. Only `double_blocks` / `single_blocks` linears are trained: 112 modules at Klein 9B, 83 MB at rank 16 in bf16. | `toolkit/network_mixins.py:525-583`, `flux2_model.py:508-513` |
| config parse | Building the job (`toolkit.job.get_job`) parses and validates every section; models load only in `run()`. So `get_job` is a dry run of a job file. | `jobs/process/BaseSDTrainProcess.py` `__init__` |
| text encoder | `train.cache_text_embeddings: true` encodes every caption once, then unloads the encoder. Caption dropout is skipped while embeddings are cached. `unload_text_encoder` alone trains on the trigger word only. | `extensions_built_in/sd_trainer/SDTrainer.py:307-345`, `toolkit/dataloader_mixins.py:408` |
| trigger word | A process-level `trigger_word` is copied to each dataset and added to any caption that lacks it. | `BaseSDTrainProcess.py:131,158`, `toolkit/dataloader_mixins.py:394-399,445` |
| resume | A job resumes from the newest LoRA in `<training_folder>/<name>/`, including a finished one, so retraining from scratch means deleting that folder. | `BaseSDTrainProcess.py:1858-1862` |
| Hugging Face | `run.py` loads `<root>/.env` (e.g. `HF_TOKEN=…`) and turns on `hf_transfer` (installed in the env). | `run.py:5-6` |

### Verifying a change

| Changed | Run | Time |
|---|---|---|
| Text rendering / bubble layout | `python tools/lettering_bench.py --sets tune` while working, then the full `python tools/lettering_bench.py` (tune, holdout, synthetic; see W7) before keeping it. `python tools/contact_sheet.py <project> --reletter` re-letters any one project | ~20s / ~75s |
| Anything upstream of generation (bridge, refs, `refs`/`wan2gp` config) | `python tools/golden_run.py --force --out <name> --baseline w4_prompts [--set KEY=VALUE ...]` | ~10 min on AC (582s measured); ~23 min on battery |
| Character descriptions or the refiner | the same with `--refine`, then freeze `tests/e2e/outputs/golden/<name>/project/prompts/*.txt` into `tests/e2e/fixtures/golden_prompts/` if it wins | + ~1 min |
| A character LoRA (training settings, its training set) | `python tools/train_lora.py train <project> <ID> --force`, then a golden run with `--lora <ID>=<path>` (W11, step 4) | training: unmeasured; + ~10 min |
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
  `live_queue/`, and the lettering bench's `holdout/` and `synthetic/`;
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
11. **Don't fit the test story.** Tune on one set and confirm on others it
    never saw (the lettering bench's holdout and synthetic sets). Reject
    signals that only work because of this cast or script.

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
