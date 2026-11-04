# lazycomics — plan

_Restocked 2026-11-02 to put the e2e faults first and all LoRA work last.
W7 and W8's as-built records (lettering, generation fixes) are in
[`archive/2026-11-03_PLAN_W7-W8.md`](archive/2026-11-03_PLAN_W7-W8.md), W5–W6
in [`archive/2026-11-02_PLAN_W5-W6.md`](archive/2026-11-02_PLAN_W5-W6.md),
and W0–W4 in [`archive/2026-11-01_PLAN_W0-W4.md`](archive/2026-11-01_PLAN_W0-W4.md).
On 2026-11-03 the leftover generation faults became W11 (Refinement) and
the LoRA work moved from W11 to W12; the archives still call it W11. W9
closed the same day; its record is in Reference, "Upscaler (from W9)".
On 2026-11-04 `tests/` was reorganised and "golden run" became "A/B run";
old paths and names map in Reference, "Where runs live". W10 closed the
same day; its record is in Reference, "Speed settings (from W10)"._

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
1. Sign off a full e2e run, every stage, reviewed by you (milestone).
2. Refinement (W11): the generation-quality faults moved out of W8 so it
   could close.
3. LoRA work (W12), last: only after the e2e is signed off and W11 is
   done, on its own branch.
   The pipeline aims for consistency without LoRAs, to avoid the training
   cost.

| Workstream | Status |
|---|---|
| W0–W4 | **Done** 2026-10-30 to 2026-11-01: the generation contract, the verification loop (`tools/ab_run.py`), bubble layout, and character consistency (52/52 appearances on-model in the e2e). Archived. |
| W5 Inpaint path | **Closed** 2026-11-02 with nothing built. Its fix (problem 6) is in W12. |
| W6 LoRA training pipeline | **Done** 2026-11-02: built and unit-tested; Wan2GP loads AI Toolkit's LoRA format (checked on real pixels). No LoRA trained: that's W12. |
| W7 Lettering | **Done** 2026-11-03 on your read of the re-lettered e2e pages: no overlaps, faces uncovered, tails on every matched speaker, the lettering bench. Archived. |
| W8 Generation fixes | **Done** 2026-11-03 on your read. Kept: dialogue and SFX as refiner context, rule checks on every refined prompt, the group cue where two or more speak, signage reworded in descriptions, two `story.cbml` edits, the refiner bench and a held-out script. Rejected: NAG, 8 steps. Leftovers moved to W11. Archived. |
| W9 Upscaling | **Done** 2026-11-03: panels are upscaled 4x before lettering (`realesrgan-x4plus-anime`, ONNX on DirectML, your pick of four), lettered at that size with the layout unchanged, and assembled into 3000px pages for the `.cbz` plus 6000px print pages. Ran in the full e2e (2026-11-03). In Reference, "Upscaler (from W9)". |
| **e2e sign-off (milestone)** | **Next.** A full e2e run, every stage, meeting W7–W9's done-whens and your review. The run at W10's defaults (2026-11-04, 28:06, 32/33 clean) ran every stage and awaits your read. The gate for W11 and W12. |
| W10 Tidy-up | **Done** 2026-11-04 on your sign-off, after the e2e at its defaults ran in 28:06 (41:45 before). Unused model fields removed; refiner thinking off with a new style rule (e2e refinement 13.5 → 3.8 min); refs characters first, capped at 4 (generation −11% on the subset); `llm.url` on `127.0.0.1`; lettering bench ~113s → ~74s. New A/B baseline `w10_refs_max4`. In Reference, "Speed settings (from W10)". |
| W11 Refinement | Not started; after the sign-off. The generation-quality faults moved out of W8: painted bubbles (problem 8), 3 of 4 drawn on the 0.22 strip and `page_2_panel_1`'s residue (problem 16). Added 2026-11-04 from your read of the W9 e2e: the Intern drawn as a second Ranjeet (`page_4_panel_3`). |
| W12 LoRA path | **Parked** until the e2e sign-off and W11: the last workstream, on its own branch. Train and judge one character's LoRA, then fix the inpaint path (problem 6). |

## Where things stand (2026-11-04)

- **Unit tests:** 482 pass (`pytest tests --ignore=tests/e2e`, ~20s).
- **Full e2e (2026-11-04, W10's defaults):** every stage ran on real Wan2GP
  + Ollama, upscaling included. It's in `tests/outputs/e2e/` for your
  read, the candidate for the sign-off.
  - `story.cbml`, 33 panels → 10 pages (2000×3000, plus 4000×6000 print
    copies) → a 52 MB `.cbz`, in **28:06** on AC (W9's run: 41:45):
    - refinement 3.8 min (13.5); 32 of 33 panels retried, all for the
      style's last clause (W10.2) and one also for a quoted SFX; none fell
      back to the original; every prompt keeps the whole style (none did
      before);
    - generation 21.3 min (24.8), 101 refs (121);
    - upscaling 1.5 min.
  - Checks: 32/33 panels clean. `page_3_panel_1`: line 4's tail points
    through line 2 (the layout on new art; the same panel as the subset's
    `page_1_panel_1` in `w10_defaults`). W9's two faults are gone.
  - My read of all 10 pages: no fault new with W10. The cast is on-model
    on `sheet_characters.png`. The W11 items, on this run, are under W11.
- **Earlier e2e runs**, in `tests/archive/e2e/`:
  - `2026-11-03_w9/`: W9's run, at the old defaults; your partial read of
    it (pages 3 and 4) gave two W11 items. 31/33 clean.
  - `2026-11-01/`: kept as it was generated, the "before" every problem
    below cites. 41.5 min on AC: 13 min of LLM refinement, 27 min of
    generation. Characters on-model in all 52 appearances where they're
    drawn. Your review found the lettering the weakest part (W7), plus
    faults in the art itself (W8).
  - `2026-11-02_w7_relettered/`: the same panels re-lettered by W7,
    30/33 clean; four speakers with no tail because their face wasn't
    matched (fail closed).
- **A/B baseline:** the A/B run `w10_refs_max4` (12/12 clean on the
  checks), since W10 changed the ref defaults: the frozen prompts in
  `tests/fixtures/main_story/subset_prompts/` at today's defaults. The
  previous baselines: `w8_frozen` (W10's runs were judged against it) and
  `w4_prompts` (W8's).
- **W8's regenerated story panels**, from before the 2026-11-03 e2e ran its
  code and the two `story.cbml` edits (`page_2_panel_1`'s background
  characters, `page_5_panel_1`'s neon), are in `tests/archive/ab_runs/`,
  each with a `compare.png` (e2e | new raw | new lettered):
  - `2026-11-03_w8_story`: the 7 bar panels, `page_2_panel_1` and
    `page_7_panel_1`;
  - `2026-11-03_w8_nocue`: the two panels the cue rule changed;
  - `2026-11-03_w8_script`: the two panels the script edits changed.
- **Held-out checks** for lettering, prompts and generation: see Reference,
  "Benches and held-out sets".
- **Generate on AC power.** On battery the laptop caps the GPU, and large
  panels take 2–2.5× as long.
- **LoRA pipeline:** built (W6) and unused; LoRA-free panels never touch it.

## Open problems

Numbers carry over from the archived plans; code and tools cite them.

| # | Problem | Evidence | Cause / lead | Fix in |
|---|---|---|---|---|
| 4 | Bubble tail points at the wrong character | Residual after W7 (2 of 30 labelled speakers): e2e `page_10_panel_1` and subset `page_4_panel_1`, the 4-character 0.22 strip. Gerald and Yuki are seen from behind, and David is drawn where the script predicts Ranjeet, so David's tail lands on Ranjeet. | Matching is by predicted position only since W7.2 dropped SFace. Position can't tell a mirrored cast when some of it isn't visible. Fixing it needs an identity signal that works on this art (none known; the colour spike in W7 only exploited this cast's hair and skin). W8's group cue asks for the predicted order: regenerated, story `page_10_panel_1` draws David on the right, where the lettering expects him. Re-check at the sign-off. | Deferred (identity); W8 may help |
| 6 | Inpaint settings never reach Wan2GP | Not exercised since May: `auto` sends LoRA-free panels through a single pass. | Inpaint tasks don't send `settings_version`, so wgp forces `denoising_strength = 1.0`. `masking_strength` 0.3 locks the area outside the mask for only ~2 of 4 steps, and flux2 never pastes the original pixels back. Net effect: the whole panel is regenerated. | W12 |
| 14 | Face detector misses stylised faces and accepts junk | An earlier A/B run's `page_6_panel_1`: Gerald's grinning face missed, a 0.53-score "face" on the floor accepted. e2e `page_5_panel_1` (three at a bar, no dialogue): no faces found. | Hybrid YuNet + MediaPipe at threshold 0.5. Tails, attribution, face avoidance and the character-sheet crops all inherit it. W7 traced no lettering fault to it. | Deferred |

**Moved to W11, Refinement** (2026-11-03, your call): 8 painted
bubbles · 16 characters not drawn on the 0.22 strip, `page_2_panel_1`'s
residue.

**Closed** (details in the archives): 1 panel dimensions · 2 text cut off at
panel edges · 3 bubble reading order · 5 character drift · 7 gibberish
signage (W8) · 9 panels were JPEGs named `.png` · 10 fragile output pairing ·
11 slow generation (power state, not code) · 12 tiny text in narrow panels ·
13 colliding text lines · 15 thought dots tiny and bunched (W7) · 17
captions overlapping (W7) · 18 bubbles covering faces (W7) · 19 the upscale stage never ran (W9) · 21 tails
missing or too small (W7) · 23 unclear conversation order, tails crossing
bubbles (W7) · 24 SFX on faces (W7) · 25 actions implied only by SFX or
dialogue (W8) · 26 the checks missing lettering faults (W7) · 27 e2e pages
reviewed at 533×800 (W7).

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

### Milestone — e2e sign-off

The gate before any LoRA work: the whole pipeline, every stage, working
properly.

1. **Full e2e on AC:** `pytest tests/e2e/test_integration_e2e.py -v -s`,
   then `python tools/contact_sheet.py tests/outputs/e2e`, then your
   read of every page. Read `pages/` (the `.cbz`) and spot-check
   `pages_print/`.
   - The run at W10's defaults (2026-11-04, 28:06) is in
     `tests/outputs/e2e/`, with its sheets: what's left is your read.
   - W9's run (2026-11-03) is archived as `tests/archive/e2e/2026-11-03_w9/`.
     You read its pages 3 and 4; both faults went to W11.
2. **Keep it.** Move the signed-off run to
   `tests/archive/e2e/<date>_signoff/`. W10 made `w10_refs_max4` the A/B
   baseline; replace it again only if something later changes the
   subset's output.

**Done when:**
- every stage ran, upscaling included, and the `.cbz` opens in a viewer;
- the checks meet W7's done-when;
- your review finds none of the W7 or W8 faults, or you've moved what's
  left to "left as is" or W11.

### W11 — Refinement. After the e2e sign-off.

The generation-quality faults you moved out of W8 so it could close
(2026-11-03), to revisit once the pipeline is signed off. They don't block
the sign-off. Judge each fix as W8 did: an A/B run of the subset or the
story on AC power, then the held-out script (Reference, "Benches and
held-out sets").

**Done when:** each item below is fixed on real pixels, or you've moved it
to "left as is".

- **3 of 4 characters drawn on the 0.22 strip** (from problem 16). Subset
  `page_4_panel_1`, which is story `page_10_panel_1`, draws 3 of 4 in every
  run since W8 (2 of 4 before; Ranjeet is the one dropped).
  - The group cue names all four, left to right. No prompt lever is known
    beyond that: four people across a strip 0.22 wide is the hard case.
  - Levers to try: fewer characters in that panel, or a wider slot (both
    script edits), or a LoRA identity (W12).
  - Lettering still points both tails right, because both speakers are
    drawn (`w8_frozen`, `w8_story`).
  - 2026-11-04 e2e (W10's defaults): still 3 of 4, Ranjeet dropped.
- **Speech bubbles and text painted into the art** (problem 8). The image
  model draws its own bubbles, blank or with gibberish, which the lettering
  can't move or remove.
  - Where they still are: 2 of 12 subset panels in `w8_frozen`
    (`page_1_panel_1`, small, above a background worker; `page_1_panel_3`,
    "MEMK." above Ranjeet), and 1 of 12 in a fresh refined run
    (`w8_refined`).
  - Subset `page_1_panel_3` is story `page_3_panel_3` (Ranjeet scrolling
    memes), which has a painted bubble in every run of it checked: the June,
    2026-11-01 and 2026-11-03 e2e runs, and `w4_prompts`, `w8_frozen` and
    `w8_refined`. Same seed and near-identical prompts give the same
    composition, so it recurs rather than being bad luck.
  - W10's defaults (thinking off, the style rule): no painted speech bubble
    on it in `w10_defaults` or the 2026-11-04 e2e, the first runs without
    one. Ranjeet holds a printout with scribbles instead. Two runs; nothing
    has isolated which change did it. That e2e's other painted text: a
    small "?" on `page_9_panel_1`, and the props on page 7 (the
    investigation board, the ID card, a "Vampire identifiation" printout).
  - The e2e's `page_8_panel_2` had two ("!!/", "ちっ!"), gone with W8's
    prompts.
  - Props invite text too: story `page_7_panel_1`'s investigation board was
    covered in gibberish in `w8_sfx2`, with a few scribbles in `w8_story`.
  - NAG removes the bubbles but duplicates characters (W8, rejected).
  - Leads:
    - reword actions and props that invite text ("scrolls memes" on
      `page_1_panel_3`, boards of notes, letters), as rewording did for
      signs;
    - the style reference (`main_story/Style.png`) is itself a comic panel
      with three lettered speech bubbles, and it goes into all 33 panels'
      refs. Untested (found 2026-11-04): A/B the subset with a bubble-free
      style image;
    - detect a painted bubble and reroll the seed.
- **The Intern drawn as a second Ranjeet** (story `page_4_panel_3`; your
  read, 2026-11-04). Script: `chars: GERALD, YUKI, RANJEET, INTERN`, and
  "Gerald appears beside the intern with supernatural speed carrying a first
  aid kit".
  - 2026-11-03 e2e: four people, but the fourth, in the Intern's slot, wears
    Ranjeet's outfit (white shirt, rolled sleeves, dark trousers) and has
    his face without the stubble. The lettering is right for the slot: the
    Intern's tail points at the double.
  - 2026-11-04 e2e (W10's defaults): the same double again.
  - 2026-11-01 e2e (before W8's group cue): three people, no Intern at all.
    The cue ("four people, left to right: …, each clearly visible") is the
    likely reason a fourth person now appears. A pixel A/B hasn't isolated
    it.
  - Leads, untested:
    - `INTERN.txt` is the only cast description with no age band, hair,
      skin or outfit colour (the consistency bar's rule), and the Intern has
      no reference image (left as is, 2026-11-02). Of the four, only he has
      neither, so the model may borrow a referenced character's look. A
      fuller description is the cheapest A/B and keeps him text-only;
    - Ranjeet is in `chars:` but the action never places him, the
      `page_2_panel_1` lesson;
    - the action puts Gerald beside the Intern, but the cue orders Gerald
      first and the Intern last, at opposite ends.
- **Story `page_2_panel_1`'s residue** (problem 16).
  - After the `story.cbml` edit placing Yuki, Ranjeet and David in the
    background, it's no longer a line-up (`w8_script`). But the panic the
    action describes is mostly gone, and a second grey-bearded man sits
    beside Ranjeet.
  - Before the edit, every W8 run posed all four in a row, with the group
    cue or without it.
  - 2026-11-04 e2e: no line-up, but two older men stand in David's glass
    office, where the script has only David.
  - Lesson: when a panel's `chars:` lists characters its action doesn't
    place, say where they are.

### W12 — LoRA path. Parked until the e2e sign-off and W11.

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
- **GERALD's training set** is generated (A/B run `w6_gerald`, 20 images
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
   `tests/outputs/ab_runs/w6_gerald/project/assets/characters/GERALD/lora_dataset_sheet.png`.
3. **Trial run**, to measure VRAM and time per step and confirm a trained
   LoRA loads: `python tools/train_lora.py train
   tests/outputs/ab_runs/w6_gerald/project GERALD --set
   ai_toolkit.steps=100`. If it runs out of memory, drop 1024 from
   `ai_toolkit.resolutions` first, then try `layer_offloading: true`.
4. **Full run and judge it.** The same command with `--force` (2000 steps),
   then `ab_run.py --lora GERALD=<path> --set
   enricher.multi_char_strategy=single --baseline w10_refs_max4`.
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
- an A/B run with it is at least as consistent as `w10_refs_max4`
  (Reference, "Consistency bar");
- a 2-character panel with that LoRA comes out of the inpaint path without
  the "two disjointed halves" seam.

### Deferred (no consumer, or not worth it yet)

- Stay-resident Wan2GP worker (old §14.10). W10.3 brought an A/B run to
  ~7.4 min; ~35s of each is loading the model. Reconsider if iterations
  still feel slow.
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
white-haired in 2 of 7 subset panels.

### Prompt rules (from W4 and W8)

What the image model is told, and what makes each rule hold. The local
refiner (gemma4 8B) doesn't reliably follow a rule that sits only in its
system prompt: W8's signage rule never fired, and the dialogue rule only
worked inline. So a rule counts as in place only where code builds it or
checks it.
- **Builders:** `prompt_builder` (the template),
  `llm_refiner._format_context` (what the refiner sees).
- **Checks:** `llm_refiner._rule_breaks`, run on every refined prompt (retry
  once, then keep the original).
- **Tests:** `tools/refiner_bench.py` scores the checks on scripts the rules
  weren't tuned on.

| Rule | Evidence | Built by | Checked by |
|---|---|---|---|
| Each character's description verbatim, labelled `Name (…)` | It's how the model tells the cast apart (W4: no hair colour gave a white-haired Gerald). Kept in 145 of 145 refined prompts on disk. | `_char_to_prompt_text` | `_rule_breaks` `description` |
| Group cue, "N people, left to right: …, each clearly visible", in `chars:` order, on panels with 3+ characters of whom 2+ speak | Draws the cast in the order the tails assume: subset `page_4_panel_1` 2 → 3 of 4, held-out cue panels in order. Doesn't stop a crowd shot posing in a row (problem 16). | `group_cue` | `_rule_breaks` `cue` |
| The cue first in the prompt | Asked for by the system prompt; no evidence it matters. `page_2_panel_1` lined up with the cue first and with it last. | system prompt only | not checked (the refiner bench counts it: 3 of 7 thinking on, 7 of 7 off) |
| The style descriptor verbatim (it may move) | Refinement trimmed it: of its 6 clauses, thinking-on prompts kept ~4, thinking-off ~3 (W10.2). The thinking-off A/B's two panels that lost "moody noir lighting…" came out lighter (not isolated). | system prompt | `_rule_breaks` `style` |
| Dialogue and SFX never quoted | Quoted text gets painted (problem 8): "removing his office tie (KRRK)". | inline reminder in the context | `_rule_breaks` `quoted` |
| Dialogue and SFX shown as action | David writes under SCRIBBLE; shouted lines give shouting faces; a hand on the door under KNOCK KNOCK. | `_format_context`, inline | can't be checked |
| No signs, posters, banners or billboards | Filled with gibberish (problem 7). Rewording `THE_BAR.txt`: lettered signs in 7 of 7 story bar panels → 1 of 7. | the descriptions (README, "Writing descriptions") | `build_prompts` warns on the input; `_rule_breaks` `signage` rejects any the refiner adds |
| LoRA trigger tokens verbatim | They're model tokens, not words. | `_char_to_prompt_text` | not checked: no LoRAs in use (W12) |
| No invented details, no quality boilerplate, under ~75 words besides descriptions | Flux ignores boilerplate. | system prompt only | not checked |
| Negative prompt off (NAG 1.0) | NAG 1.5–3 removed painted bubbles but duplicated characters (W8). | `wan2gp.nag_scale` | n/a |

Leads, not rules yet (both in W11):
- props and actions that invite text (boards of notes, letters, "scrolls
  memes"; problem 8);
- saying in the action where background characters are (problem 16). In
  one run (`w8_script`) it ended `page_2_panel_1`'s line-up.

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
- `NAG_scale` only when `wan2gp.nag_scale > 1`: off by default, since W8's
  A/Bs found it duplicates characters on Klein 9B.

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

### Upscaler (from W9)

**What runs.** `upscale_panels` enlarges each generated panel 4x
(`panels/` → `panels_upscaled/`) with `realesrgan-x4plus-anime` (BSD-3),
exported to ONNX by `tools/export_upscaler_onnx.py` and run in-process by
`onnxruntime-directml` on the GPU, in 384px tiles with 24px of context.
Install steps and times: README, "Upscaling". The model files are in
`%LOCALAPPDATA%\lazycomics\upscalers\`, with the three it was compared
against, so any of them can be tried through `upscaler.model_path`.

**Why before lettering.** Panels are generated at 1024px. Filling a 3000px
page's slots took 0.96–2.96x of Lanczos (median 1.46x), after lettering, so
the lettering was stretched too. A 2000×3000 page is already ~300 DPI at US
comic trim size; the old docstring's "4x pages are print-ready at 300 DPI"
(8000×12000) was ~27×41in. So the 4x comes before lettering, and assembly
writes two page sets (your call, 2026-11-03): `pages/` at 3000px for the
`.cbz`, and `pages_print/` at 6000px, ~600 DPI. At print size the five
panels stretched 2.93x on screen still get ~1.5x of Lanczos on top.

**Lettering the upscaled copy** (`text_renderer._TeeDraw`). The layout is
decided on the generated panel exactly as before: faces, busyness, text
measured at panel size. Every draw call is then replayed on the upscaled
copy with its coordinates, widths and font size scaled. Checked on real
output: all 33 e2e layouts are identical with and without the upscaled
copy, and the lettering bench's holdout and synthetic columns are unchanged.
The SFX outline is now a font stroke rather than 12 offset copies, so it
stays solid at 4x.

**The comparison** (2026-11-03): 4 e2e panels on AC power, RTX 3080 Laptop,
DirectML. The sheets are in `tests/outputs/reviews/upscale_compare/`, shown at
the panels' print and screen sizes. Fidelity is the PSNR of each output
shrunk back to the original: how much colour and content it keeps.

| Model | Scale | s/panel | Fidelity (dB) | Read |
|---|---|---|---|---|
| **realesrgan-x4plus-anime** (chosen) | 4x | 2.0 | 32.6 | Your pick: good on every sheet, and fast. Flatter shading than the original, brighter whites, a few invented creases. BSD-3. |
| 4x-IllustrationJaNai V1 ESRGAN | 4x | 5.7 | 37.4 | The crispest at print size; my recommendation. CC-BY-NC-SA. |
| 4x-WTP-UDS-Esrgan | 4x | 5.8 | 32.3 | Shifts colour, invents lines. CC-BY. |
| 2x-AnimeSharpV3 | 2x | 1.5 | 38.4 | The most faithful, but soft at print size, where Lanczos does up to 2.9x more. CC-BY-NC-SA. |
| Lanczos (before W9) | – | – | – | Soft art; stair-stepped bubble edges and soft text once stretched. |

**Cost in the e2e (2026-11-03):** 97s to upscale 33 panels (3.0s each with
the PNG write, ~10s a page). From the dry run on the W7 e2e: lettering at 4x
adds ~2.4s a page and the print pages another ~2.4s. About 6 MB on disk per
upscaled panel and per lettered panel; the `.cbz` is 50 MB for 10 pages.

### Speed settings (from W10)

W10 closed 2026-11-04 on your sign-off, after the e2e at its defaults ran
in 28:06 (W9's: 41:45). Measured on AC power. Its runs are in
`tests/archive/ab_runs/2026-11-04_w10_*/` (the baseline, `w10_refs_max4`,
stays in `tests/outputs/ab_runs/`), and the comparison sheets are in
`tests/outputs/reviews/w10_refs/`.

**W10.1, unused model fields.** `PanelGenerationRequest` lost
`pixel_rect`, `occlusion_order`, `composition_flags`, `panel_context`,
`pose_ref`, `lora_stack`, `prompt` and `negative_prompt`, and `PageLayout`
is gone. Requests are only written to `enriched/*.json` and read back as
dicts, so older projects with the keys still load. `w10_base` (frozen
prompts, old ref defaults) matched `w8_frozen` exactly in all 12 panels.

**W10.2, the refiner: thinking off, with a style rule.**
- The cause wasn't the context size: at 8k and at Ollama's default 131k,
  gemma4 sits 100% on the GPU at ~50 tokens/s. ~80% of each reply
  (750–1,300 tokens) went on hidden reasoning before the prompt.
- Thinking is off by default: `refine_prompts_with_llm(think=...)` →
  `llm.think` → off. Off sends `reasoning_effort: "none"` (OpenAI's field,
  which Ollama honours on `/v1`; its own `think` flag isn't). `--think` /
  `--no-think` on `ab_run.py` and the refiner bench.
- `localhost` cost ~2.05s per request (Windows tries IPv6 first; Ollama
  listens on `127.0.0.1` only), so `llm.url` uses `127.0.0.1`.
- Thinking off alone (`2026-11-04_w10_nothink`) trimmed more of the style
  descriptor (~3 of its 6 clauses kept, ~4 thinking on, and nothing
  checked it). Its A/B had more painted bubbles on `page_1_panel_1` and
  Ranjeet's `page_1_panel_3`, your read of what mattered, and those panels
  came out lighter. Hence the `style` rule (Reference, "Prompt rules").
- Refiner bench, thinking off with the rule: 19 of 23 clean first time
  (22 thinking on, 17 off without the rule), 0 originals kept, 75s
  against ~9 min. Thinking-on results:
  `tests/archive/refiner_bench/2026-11-03_thinking_on/`.
- On the main story every first reply drops the style's last clause,
  "contemporary urban setting", probably read as the setting, and the
  retry restores it: a second ~3s call per panel. Rewording `Style.txt`
  would save it; left as is, since the script is yours.
- At the new defaults (`2026-11-04_w10_defaults` and the e2e): no painted
  speech bubble on Ranjeet's panel, and the dark noir look of the
  thinking-on runs. E2e refinement 13.5 → 3.8 min.

**W10.3, the refs: characters first, capped at 4.**
`prepare_references(max_refs=...)` → `refs.max_refs` → 4 (0 = no cap),
with `characters_first: true` and `closeup_location: false`; `--max-refs N`
on `ab_run.py`. With characters first, the cap drops the style and location
refs before any character's. Frozen prompts, `--force`; per-panel times
summed, since the first run paid ~68s of cold model loading and the others
~35s:

| Run | Refs (12 panels) | Wall | Per-panel sum | Checks |
|---|---|---|---|---|
| `w10_base` (old defaults) | 46 | 522s | 454s | 12/12 |
| `w10_refs_v1`: `characters_first`, no `closeup_location` | 42 | 480s | 446s (−2%) | 11/12 (problem 4 on the strip) |
| `w10_refs_max4`: the same + `max_refs: 4` | 37 | 441s | 405s (−11%) | 12/12 |

- The savings are on the 5–6-ref panels (subset `page_2_panel_4` 79s →
  56s); close-ups without the location save ~4s each. `max_refs: 3` would
  drop a character, so it wasn't run.
- The consistency bar held on your read and mine, with the baseline's two
  strip misses; style and setting unchanged on the capped panels.
- An A/B run is ~7.4 min warm, under W2's 8-minute target. E2e generation
  24.8 → 21.3 min.
- `w10_refs_max4` became the A/B baseline: the frozen prompts at these
  defaults.

**W10.4, the lettering bench** letters copies of its tune set at 1x under
`tests/outputs/lettering_bench/tune/`, as it does the holdout set
(`lettering_bench._copy`). Tune 61s → 22s, the bench ~113s → ~74s, every
set's output identical line for line. It no longer writes the e2e or
`w4_prompts`.

### Benches and held-out sets (from W7 and W8)

You asked for these on 2026-11-02, so that fixes don't overfit the test
story (e.g. by relying on this cast's distinct skin tones). Each judges a
change beyond the panels it was tuned on. Their first results are in the
W7–W8 archive.
- **Lettering bench** (`tools/lettering_bench.py`, ~75s, no GPU). It
  re-letters copies of three sets at 1x and tallies the checks, without
  writing the projects it copies:
  - *tune*: the e2e and `w4_prompts`;
  - *holdout*: copies of the three older archived runs (57 panels,
    different art), checked only after a change, never while making it;
  - *synthetic*: seeded random comics run through the real pipeline, varying
    page layouts, aspects, casts, text within a realistic word budget, every
    bubble type, captions, SFX and face sizes.
  
  It also scores attribution against hand labels
  (`tests/fixtures/face_labels.json`, 20 labelled panels keyed by image
  hash).
- **Refiner bench** (`tools/refiner_bench.py`, ~75s with thinking off, ~9
  min with `--think`; LLM only). It refines
  a seeded synthetic script (its own cast, places, actions, dialogue and
  SFX) and the held-out script. It scores each first reply against
  `_rule_breaks` (Reference, "Prompt rules"), and counts retries and
  fallbacks to the original.
- **Held-out script** (`tests/fixtures/holdout_story/`, 7 panels).
  - Nova and Rex use the June fixture's refs; Marlo has none. The setting
    is a steampunk alley and a clock workshop, in a noir style.
  - It covers both cue cases, a no-dialogue group panel, SFX and a shout
    that imply actions, and a prop that invites text.
  - Run it with `ab_run.py --story tests/fixtures/holdout_story
    --refine --force --out <name>` (~8 min on AC).

### Verifying a change

| Changed | Run | Time |
|---|---|---|
| Text rendering / bubble layout | `python tools/lettering_bench.py --sets tune` while working, then the full `python tools/lettering_bench.py` (tune, holdout, synthetic; see "Benches and held-out sets") before keeping it. `python tools/contact_sheet.py <project> --reletter` re-letters any one project itself, the e2e at 4x | ~25s / ~75s |
| The upscaler (model, tiling) or print pages | On a copy of the e2e project: `lc.upscale_panels(project, force=True)`, `lc.render_text(project, force=True)`, `lc.assemble_pages(project, force=True)`. The layout records must not change; compare crops of `pages/` and `pages_print/` with the e2e's | ~3 min |
| Anything upstream of generation (bridge, refs, `refs`/`wan2gp` config) | `python tools/ab_run.py --force --out <name> --baseline w10_refs_max4 [--max-refs N] [--set KEY=VALUE ...]` | ~8 min on AC (441s of generation at W10's defaults, 2026-11-04); ~20 min on battery |
| Character descriptions, the prompt template or the refiner | `python tools/refiner_bench.py` (LLM only) while working. Then an A/B run with `--refine`, and the held-out script: `python tools/ab_run.py --story tests/fixtures/holdout_story --refine --force --out <name>`. Freeze `tests/outputs/ab_runs/<name>/project/prompts/*.txt` into `tests/fixtures/main_story/subset_prompts/` if it wins | ~75s / + ~1 min / ~8 min |
| A character LoRA (training settings, its training set) | `python tools/train_lora.py train <project> <ID> --force`, then an A/B run with `--lora <ID>=<path>` (W12, step 4) | training: unmeasured; + ~10 min |
| Before a release / after contract changes | `python tests/smoke/two_panels.py` (2-panel smoke test, minutes), then `pytest tests/e2e/test_integration_e2e.py -v -s` (replaces `tests/outputs/e2e/`: archive a run worth keeping first) | ~30 min on AC (28:06 on 2026-11-04) |

`ab_run.py` writes `report.txt` (`review.check_panel`: aspect, text cut
off by the slot, reading order, overlaps, faces covered, tails),
`sheet_pages.png` and `sheet_characters.png`. `tools/contact_sheet.py
<project_dir> [--baseline <project_dir>]` writes the same sheets and checks
for any already-rendered project, e.g. after a full e2e run.
- The checks can't judge tail *correctness* or likeness; the sheets are for
  that.
- Panels are kept between runs, so pass `--force` whenever generation inputs
  change, or stale panels get reviewed.
- The subset, and why each page is in it: `SUBSET_PAGES` in the tool.

**Where runs live.** The tree is in the README's "Development" section.
Since 2026-11-04 `tests/e2e/` holds the end-to-end test and nothing else:
- inputs are in `tests/fixtures/` (tracked): `main_story/` (the test comic,
  its subset and frozen prompts), `holdout_story/`, `face_labels.json`;
- what each test or tool writes goes to its own folder in `tests/outputs/`:
  `e2e/`, `smoke/`, `ab_runs/<name>/`, `lettering_bench/`, `refiner_bench/`,
  and `reviews/<name>/` for one-off comparison sheets;
- runs kept on purpose go to `tests/archive/`, in the same layout:
  `e2e/<date>[_label]/`, `ab_runs/<date>_<name>/`,
  `refiner_bench/<date>_<label>/`. Both are gitignored.

`ab_run.py` takes a bare run name for `--out`/`--baseline` (a folder in
`tests/outputs/ab_runs/`), or a path. `tests/outputs/ab_runs/` keeps only
the runs still in use: `w10_refs_max4` (the A/B baseline), `w8_frozen`
(the previous one), `w4_prompts` (the lettering bench's tune set),
`w6_gerald` (W12's training set) and `holdout_story` (the latest held-out
run). W8's and W10's other runs are in the archive. Names and paths in the
archived plans, by current location:

| In the archived plans | Now |
|---|---|
| golden run, golden subset, `tools/golden_run.py`, `GOLDEN_PAGES` | A/B run, the subset, `tools/ab_run.py`, `SUBSET_PAGES` |
| golden run `<name>` (`tests/e2e/outputs/golden/<name>/`) | `tests/outputs/ab_runs/<name>/` for the four above; W8's others in `tests/archive/ab_runs/<date>_<name>/` (e.g. `w8_nag2` → `2026-11-02_w8_nag2`) |
| `golden_w4_prompts/` | A/B run `w4_prompts` (the baseline until W8; now `w8_frozen`) |
| `golden_output/` (2026-10-30 baseline), later `archived_test_outputs/2026-10-30_golden_baseline/` | `tests/archive/ab_runs/2026-10-30_baseline/` |
| `golden_w3/` (W4 rung 1), later `archived_test_outputs/2026-10-30_golden_w3/` | `tests/archive/ab_runs/2026-10-30_w3/` |
| `test_e2e_output/` (June e2e run), later `archived_test_outputs/2026-06-10_e2e/` | `tests/archive/e2e/2026-06-10/` |
| `archived_test_outputs/2026-11-01_e2e/`, `…/2026-11-02_e2e_w7_relettered/` | `tests/archive/e2e/2026-11-01/`, `…/2026-11-02_w7_relettered/` |
| `tests/e2e/outputs/e2e/` | `tests/outputs/e2e/` |
| `tests/fixtures/integration/`, later `tests/e2e/fixtures/` | `tests/fixtures/main_story/` (cast in `characters/` and `locations/`); `holdout_story/` and `face_labels.json` beside it |
| `golden.cbml`, `golden_prompts/` | `tests/fixtures/main_story/subset.cbml`, `…/subset_prompts/` |
| `tests/e2e/fixtures/old/` (the June fixture) | Removed 2026-11-04: unused, and 4 of its 6 files are in `holdout_story/`. In git at `db76456`. |
| the lettering bench's `outputs/holdout/`, `outputs/synthetic/` | `tests/outputs/lettering_bench/holdout/`, `…/synthetic/` |
| `tests/e2e/test_wan2gp_live.py`, `…_live_queue.py`; `outputs/live/`, `outputs/live_queue/` | `tests/smoke/one_panel.py`, `two_panels.py`; `tests/outputs/smoke/` |
| `outputs/upscale_compare/` | `tests/outputs/reviews/upscale_compare/` |

**Panel settings.** Each generated panel carries the settings wgp actually
ran with, as JSON (`num_inference_steps`, `NAG_scale`, `resolution`, `seed`,
per-panel `generation_time`, …). Read them to confirm a `--set` reached wgp,
or to time panels individually:
- panels generated since 2026-11-01 (real PNGs):
  `json.loads(Image.open(p).text["comment"])`;
- older panels (JPEG bytes named `.png`, e.g. A/B run `w4_prompts`):
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
9. **Lazy-import heavy/optional deps** (`cbml_parser`, `requests`,
   `onnxruntime`). MediaPipe
   is the exception (hard dep).
10. **`_build_wangp_task` is the sole Wan2GP adapter.** Wan2GP field semantics
    live there and nowhere else.
11. **Don't fit the test story.** Tune on one set and confirm on others it
    never saw: for lettering, the lettering bench's holdout and synthetic
    sets; for prompts, the refiner bench and `fixtures/holdout_story/`.
    Reject signals that only work because of this cast or script.

### Pipeline + contracts

`enrich` → `build_prompts` → (`refine_prompts_with_llm`) →
`prepare_references` → `generate_panels` (Wan2GP) → `upscale_panels` →
`render_text` → `assemble_pages` → `export_cbz`.

Directories: `enriched/` → `prompts/` → `refs_prepared/` → `panels/` →
`panels_upscaled/` → `panels_text/` → `pages/` (+ `pages_print/`) →
`output/*.cbz`.

- **`panels_upscaled/<id>.png`** (upscaler): the panel 4x, recording the
  SHA-1 of the `panels/` image it was made from (PNG text
  `lazycomics_source_sha1`). A regenerated panel is redone by the next
  `upscale_panels`; `render_text` refuses a copy made from another image,
  and letters the panel itself if there's no copy.
- **Coordinates.** What the renderer records (`layout.json`,
  `detected_faces`, matched regions) is in generated-panel px; `layout.json`'s
  `scale` is how much larger the lettered PNG is. `panel_slots.json` is in
  lettered-panel px, and `review.check_panel` converts where the two meet.
- **`pages_print/`** (assembler): every page again at
  `assembly.print_page_height_px`, gutters scaled. Not in the `.cbz`, and no
  slot record.

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
  project means re-running `enrich` (A/B runs do) or rewriting the old
  path prefix.
