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
| **W7 Lettering** | **Next.** e2e problems 4, 15, 17, 18, 21, 23, 24, plus the review loop (26, 27). No GPU. |
| W8 Generation fixes | Not started. e2e problems 7, 8, 16, 25. GPU A/B runs; can run in the background during W7. |
| W9 Upscaling | Not started. The pipeline's last stage has never run (problem 19). Pick an upscaler and where it sits in the pipeline, then run it. |
| e2e sign-off (milestone) | Not started. A full e2e run, every stage, meeting W7–W9's done-whens and your review. The gate for W11. |
| W10 Tidy-up | Not started. Unused model fields, refinement and golden-run speed. |
| W11 LoRA path | **Parked** until the e2e sign-off; on its own branch. Train and judge one character's LoRA, then fix the inpaint path (problem 6). |

## Where things stand (2026-11-02)

- **Unit tests:** 440 pass (`pytest tests --ignore=tests/e2e`, ~1 min).
- **Full e2e (2026-11-01):** the pipeline runs end to end on real Wan2GP +
  Ollama, but isn't signed off.
  - `story.cbml`, 33 panels → pages → `.cbz`, in 41.5 min on AC power: 13 min
    of LLM refinement, 27 min of generation. Output is in
    `tests/e2e/outputs/e2e/`.
  - Characters are on-model in all 52 appearances where they're drawn.
  - Your review found the lettering the weakest part (W7), plus faults in
    the art itself (W8). Upscaling was skipped (problem 19, W9).
  - Machine checks, rerun 2026-11-02: 28/33 panels clean. Flagged:
    `page_1_panel_1`, `page_2_panel_2`, `page_4_panel_3`, `page_7_panel_2`,
    `page_10_panel_1`. The checks miss most of what your review found, and
    the pages are assembled at 533×800, not the 3000px that ships (problems
    26, 27).
- **Golden reference** for A/B runs: the golden run `w4_prompts`. Its frozen
  prompts are in `tests/e2e/fixtures/golden_prompts/`.
- **Generate on AC power.** On battery the laptop caps the GPU, and large
  panels take 2–2.5× as long.
- **LoRA pipeline:** built (W6) and unused; LoRA-free panels never touch it.

## Open problems

Numbers carry over from the archived plans; code and tools cite them.

| # | Problem | Evidence | Cause / lead | Fix in |
|---|---|---|---|---|
| 4 | Bubble tail points at the wrong character | e2e: `page_5_panel_2` (Gerald and Yuki swapped) and `page_10_panel_1` (David and Ranjeet swapped). `page_5_panel_2` is the worst case: Yuki is on the left and Gerald on the right, so position alone (Yuki listed first) was right, and SFace overruled it. Tails then hand Yuki's lines to Gerald, which reads as the conversation in the wrong order. | Matching blends position with SFace identity (W3). The W3 spike found the blend broke none of 9 panels; this panel shows it can. SFace is weak on this art (cosine 0.1–0.56), so identity can't always overrule position. | W7.2 |
| 6 | Inpaint settings never reach Wan2GP | Not exercised since May: `auto` sends LoRA-free panels through a single pass. | Inpaint tasks don't send `settings_version`, so wgp forces `denoising_strength = 1.0`. `masking_strength` 0.3 locks the area outside the mask for only ~2 of 4 steps, and flux2 never pastes the original pixels back. Net effect: the whole panel is regenerated. | W11 |
| 7 | Gibberish signage | Bar scenes: neon signs read "PICAK", "CERAID", "DORK". | Location descriptions invite signs, and the negative prompt does nothing unless NAG is on (problem 8). | W8.1 |
| 8 | Speech bubbles painted into the art; negative prompt inert | Golden subset: 3 of 12 panels at the 2026-10-30 baseline, 2 of 12 in `w4_prompts`. e2e `page_8_panel_2` (your page 9, panel 1): two painted bubbles with non-English text ("!!/", "ちっ!") beside ours. | Klein is guidance-distilled: wgp forces `guidance_scale` to 1, so the negative prompt only acts through NAG, which needs `NAG_scale > 1`. `wan2gp.nag_scale` is exposed but untested. | W8.1 |
| 14 | Face detector misses stylised faces and accepts junk | An earlier golden `page_6_panel_1`: Gerald's grinning face missed, a 0.53-score "face" on the floor accepted. | Hybrid YuNet + MediaPipe at threshold 0.5. Tails, attribution, face avoidance and the character-sheet crops all inherit it. | Deferred; watch in W7 |
| 15 | Thought-bubble trailing dots are tiny and bunched | Golden `page_1_panel_2`: three dots within ~20px of the cloud. | They share the speech tail's length cap (`tail_frac` × short edge). | W7.4 |
| 16 | Characters listed on a panel but not drawn | e2e `page_2_panel_1` (wide crowd shot, 4 listed): only Gerald is identifiable. The 4-character 0.22 strip (golden `page_4_panel_1`, story `page_10_panel_1`) drew 2 of 4 in both golden runs, all 4 in the e2e. | Prompt-dependent: crowd and group shots let the generator drop named characters or blend them into the background. | W8.2 |
| 17 | Captions overlap each other | e2e `page_1_panel_1` (688px wide): the top-left and top-right captions (353 and 333px wide) overlap, and the second hides "…by da". The checks also flag line 1 overlapping caption 2 there. | Each caption is sized and placed at its `pos:` on its own. Bubbles avoid captions; captions don't avoid each other. | W7.3 |
| 18 | Bubbles cover faces | e2e `page_1_panel_1` (Gerald's thought cloud over his face), `page_2_panel_2` (Yuki's second line over Gerald's head), `page_7_panel_2`. The checks report these as "covers a face"; your review confirmed the first two. | Not investigated: the layout's face-avoidance term didn't hold on these panels. | W7.3 |
| 19 | The upscale stage has never run | Both e2e runs skipped it. | `realesrgan-ncnn-vulkan` isn't installed or on PATH. | W9 |
| 21 | Tails missing, or too small to see | Your review: no tails on pages 4 (panel 3), 5 (panel 4) and 7 (panel 4). The checks add `page_1_panel_1` (Gerald, cause b) and `page_10_panel_1` (Gerald, 4-character strip: his face wasn't matched, cause c). | Four causes in `text_renderer`: (a) shout and whisper bubbles never get a tail, by design (`page_4_panel_3`'s "BANDAID!"); (b) the tail is only drawn up or down, so a bubble level with its speaker's face gets none (`page_1_panel_1`'s thought cloud, golden `page_1_panel_1`'s "Like 2008?"); (c) the speaker wasn't matched to a face, so the tail is left off on purpose (the Intern, text-only by decision); (d) when drawn, the tail is a fixed 14px wide and at most 5% of the panel's short edge, which vanishes at page scale (`page_5_panel_4`, `page_7_panel_4`). | W7.4 |
| 23 | Conversation flow unclear; tails cross other bubbles | `page_3_panel_1`: Gerald's "I have seen worse crashes." and "Much earlier." stack touching, with Yuki's "Like 2008?" level with the first, so his two lines read as one speech. "Much earlier."'s tail runs up through the bubble above it. | Layout keeps CBML order (W3) but has no minimum stagger between consecutive bubbles, and tails aren't treated as obstacles. | W7.3 (stagger), W7.4 (tails) |
| 24 | SFX land on faces | Your page 8, panel 2 (`page_8_panel_3`): "SKREEEE" sits across Gerald's mouth. | The script says `pos:center`; SFX are drawn exactly at their anchor and, unlike bubbles, never avoid faces. In a close-up the centre is the face. | W7.3 |
| 25 | Actions implied only by SFX or dialogue aren't drawn | `page_7_panel_1`: the SFX is "SCRIBBLE SCRIBBLE", but David isn't writing; the prompt never mentions it. | Neither the prompt builder nor the refiner sees SFX or dialogue: the prompt comes from the action, shot, mood, setting and characters only. | W8.3 |
| 26 | The checks miss most lettering faults, and misreport missing tails | Of your review's faults, the checks caught 3 face covers, 1 caption overlap and some missing tails. Every missing tail is reported as "speaker's face not found", including `page_1_panel_1`, where Gerald's face was matched (cause 21b). Shouts and whispers are never checked, so `page_4_panel_3`'s "BANDAID!" goes unflagged. | `review._tail_issues` has one message for every cause and skips non-speech types. No checks yet for tail size at page scale, SFX on faces, bubble stagger or tails crossing bubbles. | W7.1 |
| 27 | The e2e reviews pages at 533×800, not the 3000px that ships | `tests/e2e/outputs/e2e/pages/*.png`. Makes tail size (21d) look worse than it ships, and means you review a different page from the one that ships. | The e2e test assembles at a small page height for speed. | W7.1 |

**Closed** (details in the archives): 1 panel dimensions · 2 text cut off at
panel edges · 3 bubble reading order · 5 character drift · 9 panels were JPEGs
named `.png` · 10 fragile output pairing · 11 slow generation (power state,
not code) · 12 tiny text in narrow panels · 13 colliding text lines.

**Decided 2026-11-02, left as is:**
- **Speech bubbles stay rounded rectangles** (was problem 22: your review
  found them boxy on pages 2 and 7). `_draw_speech`, 20px corner radius.
- **The Intern stays text-only** (was problem 20). With no reference image
  he's drawn as a different person each time, identity matching can't place
  him, and his lines have no tail (fail closed, cause 21c).

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

### W7 — Lettering. NEXT.

Why first: your review of the e2e pages (2026-11-01) found the art and page
layout largely fine and the lettering the weakest part, and lettering is
most of the open faults. All of it is re-lettering, so there's no GPU and an
iteration takes seconds. The steps run in dependency order: who speaks,
then where bubbles go, then tails.

1. **Review loop on the e2e output** (problems 26, 27).
   - First copy `tests/e2e/outputs/e2e/` to
     `tests/e2e/archived_test_outputs/2026-11-01_e2e/`: it's the "before"
     every problem above cites, and re-lettering overwrites it.
   - Re-letter any project without a GPU. For example, a `--reletter`
     option on `tools/contact_sheet.py` could run `render_text` +
     `assemble_pages` before the checks, making the e2e output a 33-panel
     regression set. `golden_run.py --no-generate` only covers the
     12-panel golden subset.
   - Assemble the e2e pages at the production size (`assembly.*` config,
     3000px), so what you review is what ships.
   - Make the no-tail check report its cause (no matched face, bubble level
     with the face, shout/whisper) and cover shouts and whispers.
   - Add a check for each fault this workstream fixes, so it stays fixed:
     tails too small at page scale, SFX over a face, consecutive bubbles
     without stagger, a tail crossing another bubble. Caption overlaps and
     face covers are already checked.
   - Record the new checks' baseline on the 33 panels before changing any
     lettering.
2. **Attribution** (problem 4). First, because it decides which face each
   bubble is placed by and where its tail points.
   - `page_5_panel_2` shows SFace overruling a correct positional match.
   - Re-measure the blend on the e2e's 33 panels before re-tuning; the
     blend weight was chosen on 5 golden panels. Then either require a clear
     identity margin before position is overruled, or drop identity.
3. **Placement** (problems 17, 18, 23, 24):
   - captions on the same edge share its width;
   - bubbles, clouds and SFX stay off faces (SFX may move within their
     `pos:` zone);
   - consecutive bubbles get a minimum stagger, so that a speaker's two
     lines with another speaker's line between them don't read as one
     speech.
4. **Tails** (problems 21, 23, 15). Last, because they join each bubble's
   final position to a face.
   - draw tails sideways too, so a bubble level with its speaker still
     points at them;
   - give shouts a spike toward the speaker and whispers a dashed tail;
   - scale the tail's width and length with the font, so it survives page
     scale;
   - route tails around other bubbles;
   - give thought-bubble dots their own length;
   - keep leaving the tail off when the speaker isn't matched (fail closed).

**Watch:** tails, attribution and face avoidance all rely on the face
detector (problem 14). If a fault traces to a missed or junk face, pull
problem 14 into this workstream.

**Done when**, on the e2e output (at 3000px) and the golden subset:
- the checks report no overlap, face, order, tail-size or stagger issues;
- the only missing tails belong to characters whose face isn't visible or
  wasn't matched;
- your read of the assembled e2e pages finds none of: overlapping text, text
  over heads, missing or invisible tails, wrong speakers, unclear
  conversation order, SFX on faces.

### W8 — Generation fixes

The e2e faults that come from the art itself. Each item is a golden A/B run
against `w4_prompts` on AC power. Keep a change only if the consistency bar
still holds. Every knob is already a config value (`golden_run.py --set`).
W7 is CPU-only, so these runs can go in the background while W7 is under
way. The sign-off run re-checks the lettering on whatever W8 regenerates.

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
- Comic-tuned face detector (old §14.2): problem 14, which W7 may pull in.
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
| Text rendering / bubble layout | `python tools/golden_run.py --no-generate --out w4_prompts` (re-letters that run's panels in place) | seconds |
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
