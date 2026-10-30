# lazycomics — plan

_Started 2026-10-30. Supersedes everything in [`archive/`](archive/README.md)._

**How this doc works.** Status lives only in the two tables directly below:
workstreams, then problems. When a workstream closes, delete its section (git
history keeps it) and update the tables. Don't annotate sections with
"RESOLVED" notes. A claim only counts as verified when it has been checked
against pixels from a real Wan2GP run, not just unit tests. The old plan
marked things resolved on unit-test evidence alone, and two of those claims
turned out to be wrong.

| Workstream | Status |
|---|---|
| W0 Housekeeping | **Done** 2026-10-30: `test_e2e_output/` and `golden_output/` gitignored and untracked; stray script and logs removed; README and example updated. |
| W1 Generation contract | **Landed; pending a full e2e run.** Verified on 17 panels (problems 1, 2). Two small items remain (see W1). |
| W2 Verification loop | **Done and accepted** 2026-10-30. `tools/golden_run.py` regenerates the 12-panel golden subset in one command (582s from scratch, misses the <8 min target); the no-GPU loop takes 10s. See W2. |
| W3 Bubble layout | **Done 2026-10-30, pending your review.** Done-when met on the golden subset (order, tails, narrow text). Identity matching (OpenCV SFace, option a) is blended with position and downloads its model on first use. Residual: one tail-less shout and one held-out panel still mis-attributed (problem 4). See W3. |
| W4–W6 | Not started. |

---

## Where things actually stand (verified 2026-10-30)

**Code health is fine.** 372 unit tests pass (`pytest --ignore` the three live
suites, ~10s). The full pipeline ran end-to-end on real Wan2GP + Ollama on
2026-06-10: 33 panels → 10 pages → `.cbz`, output in `test_e2e_output/`.
Upscale was skipped because `realesrgan-ncnn-vulkan` wasn't on PATH.

**2026-10-30 W1 re-run.** 17 panels (pages 2, 4, 5, 6, 10, covering every slot
aspect in the story) regenerated with the W1 bridge fix. The June run's
LLM-refined prompts and seed 42 were reused, so W1 is the only variable.

**2026-10-30 golden baseline** (`golden_run.py --force`, from scratch). These
are the 12 panels of `golden.cbml`, i.e. story pages 3, 8–9 and 10,
renumbered golden pages 1, 2–3 and 4. Output is in `golden_output/`
(gitignored), the reference for later A/B runs. **Generation is
deterministic:** the three story-page-10 panels came out bit-identical to the
W1 re-run's, so a difference between two golden runs is caused by what
changed. An earlier 8-panel draft of the subset (story IDs) is cited below
as "first golden run".

Panel IDs below are **story** IDs (`story.cbml`) unless marked "golden". The
mapping is golden `page_1_*` = story `page_3_*`, golden `page_2_*` = story
`page_8_*`, and golden `page_4_*` = story `page_10_*`.

**Output quality is not.** Findings from inspecting the June run, updated with
the W1 re-run and the golden runs:

| # | Problem | Old doc said | What the 2026-06-10 output shows | Root cause |
|---|---|---|---|---|
| 1 | **Panel dimensions** — **FIXED, verified 2026-10-30.** 17/17 re-run panels came out at exactly the requested size (448×1024 where June gave 784×576, 224×1024 where it gave 592×448, 1024×512 where it gave 848×624), all within 1.6% of slot aspect. | RESOLVED | Every panel comes out ~4:3 landscape whatever was requested. A 0.44 portrait slot gets 784×576; a 2:1 slot gets 848×624; a 0.22 slot gets 592×448. The pixel *area* matches the request; the *aspect* doesn't. | **Confirmed in Wan2GP source.** The `"K"` in `video_prompt_type: KI` makes wgp take the output aspect from `image_refs[0]`, which is the style ref (1342×996). `resolution` only sets the pixel budget (`fit_canvas: 0`). The formula reproduces 784×576, 1184×880 and 848×624 exactly. No check compares *output* dims to the request, so this went unnoticed. |
| 2 | **Text cut off / bubbles in odd places** (final pages) — **FIXED with #1, verified 2026-10-30.** Re-run pages have no edge-clipped text. `page_010` panel 3 has its characters back. `page_005`'s 2:1 panels regained bubbles that cropping had removed entirely ("Later that night.", "Constantly."). | RESOLVED | On the un-cropped `panels_text/` images, bubbles are mostly well placed and fully legible. On assembled pages, text is sliced at the panel edges. | Downstream of #1: the assembler's `cover` fit crops a 1.35 panel into a 0.44 slot (keeping ~33% of the width) or a 0.22 slot (~17%). `page_010` panel 3 loses its characters entirely. |
| 3 | **Bubble reading order** — **FIXED (W3.1), verified 2026-10-30.** Order now holds by construction. Story `page_2_panel_2`, reversed in the W1 re-run, reads correctly after re-lettering, and the checks find no order or overlap issues across all 17 W1-run panels or the 12 golden panels. | RESOLVED | `page_3_panel_1`: CBML order Yuki→Gerald→Yuki→Gerald is rendered fully reversed (last line top-left, first line bottom-centre). | `_place_bubble` is greedy. The first line takes the cheapest slot, which is just above its speaker's head and therefore *low*. Each later line can only go up. The "earlier is higher" bias can't recover. |
| 4 | **Bubble tail points at wrong speaker** — **fixed for every golden tail (W3.2 + SFace identity blend), 2026-10-30; two placements remain.** Fixed: golden `page_1_panel_1` (background faces had taken both speakers' places) and `page_2_panel_2` (both tails wrong because a silent character was ignored). Golden `page_4_panel_1` (mirrored; David's tail pointed at Ranjeet) was fixed by the identity blend. **Remaining:** golden `page_2_panel_1`, a **regression vs the June baseline**: David's tail-less "EAT IT!" shout sits beside Gerald, and identity evidence is too weak to overrule position. Held-out story `page_4_panel_3`: the Intern has no reference image and the cast was reordered, so 3 of 4 faces are still mis-matched. Not machine-checkable; visible on `sheet_pages.png`. | RESOLVED | `page_8_panel_4`: David's line has its tail on Gerald. | `_resolve_speaker_anchors` sorts *speakers only* left to right and matches them to *all* detected faces sorted by x. Silent characters, background extras, or one false-positive face shift every match by one. |
| 5 | **Character drift** — **much improved on solo panels after W1 (first look, not yet measured).** `page_006`'s three Gerald-solo panels now show one consistent young man with messy brown hair. The `page_002` desk panel shows the same Gerald where June had a white-haired stranger. **But not every panel:** in the golden baseline Gerald is still a white-haired old man in the golden `page_1_panel_4` close-up (his ref passed first; the prompt calls him "a pale white hedge fund analyst", see W4.2). He has white spiky hair in golden `page_2_panel_2`. Formal re-baseline is W4.1. | OPEN | Worst on **solo** panels. Gerald's ref is young with brown hair; solo panels 3-4 and 6-1..3 show a white-haired old man and a clownish face. **Multi-character** panels are often excellent: in `page_8_panel_4` all four characters match their refs. | **Confirmed in Wan2GP source: `image_start` is silently discarded for Klein.** It's kept only if `"S"` is in `image_prompt_type`, which Klein doesn't allow. The primary character's ref goes *only* to `image_start` and is excluded from `image_refs` (`ref_preparer._collect_supporting`). So the most important character on every panel has had **no visual reference**; only the other characters (in `image_refs`) were conditioned. |
| 6 | **Inpaint quirks** ("two disjointed halves") | "Addressed, visual confirmation pending" | Not exercised since May. `enricher.multi_char_strategy: auto` routes every LoRA-free multi-char panel to a single pass, so all 33 panels in the June run were `single`. | **Confirmed in Wan2GP source: our inpaint settings never applied.** Tasks don't send `settings_version`, so `flux_handler.fix_settings` forces `denoising_strength = 1.0`, overriding our 0.25. `masking_strength` 0.3 locks the area outside the mask for only ~2 of 4 steps, and flux2 never pastes the original pixels back. Net effect: the whole panel gets regenerated, so the May "two disjointed halves" came from Wan2GP's settings, not our masks. Dormant until a character has a LoRA. |
| 7 | Gibberish signage ("PICAK", "FIOOUFEK") | — | Neon signs in the bar scenes carry nonsense text. | Location descriptions invite signage, and the negative prompt is inert (#8). |
| 8 | **`guidance_scale` and `negative_prompt` do nothing** | (bubble negatives marked as the fix for bubbles painted into the art) | Golden baseline: Wan2GP painted gibberish speech bubbles into **3 of 12** panels (golden `page_1_panel_1`, `page_1_panel_3`, `page_2_panel_2`). In `page_1_panel_1` our "Like 2008?" lands on top of one. | Confirmed in Wan2GP source. Distilled Klein 9B has no `guidance_max_phases`, so `guide_scale` is forced to 1. The negative prompt is only used through NAG, and only when `NAG_scale > 1` (default 1). Whatever stopped bubbles being painted in, it wasn't the negatives. |
| 9 | Panel files are JPEGs named `.png` | — | `panels/*.png` start with `ff d8` (JPEG). | wgp writes `.jpg`; `_collect_outputs` copies the bytes to `<id>.png`. Harmless downstream (later stages save real PNGs) but misleading. |
| 10 | Output→panel pairing is fragile | — | Worked in the June run. | `_collect_outputs` pairs by sorted filename (`<timestamp>_seed42_<prompt>.jpg`). Two tasks finishing in the same second sort by prompt text. A failed task shifts every later panel by one. |
| 11 | **Generation slower since W1; how much is unclear** | — | W1 re-run: 1597s for 17 panels (avg 83s) vs ~555s for the same panels in June. Small portrait panels ~1.7× slower (e.g. 23s → 39s); large 4–6-ref panels 3–4× (`page_2_panel_1` 71s → 213s). **But the golden runs did similar large panels much faster:** `page_8_panel_4` (6 refs, 1024×768) 92s and `page_3_panel_1` (4 refs, 912×1024) 63–68s, against ~200s for comparable panels in the 27-minute W1 run. Golden baseline: 19–92s per panel, 582s for 12. | One extra ref per panel (the primary, previously discarded) explains a moderate slowdown: each ref costs about an output image's worth of tokens. The large-panel cliff did **not** reproduce in a short run, so it isn't size/ref count alone. Candidates: laptop thermal throttling over long runs, or another GPU process (LM Studio was resident). Needs a controlled measurement before acting. Levers if real: W4.2 (fewer refs), W4.5 (lower `base_resolution` + upscale). |
| 12 | **Text tiny and over-wrapped in narrow panels** — **FIXED (W3.5), verified 2026-10-30.** Golden `page_4_panel_3`'s line went from 7 lines to 3. 0.22-slot panels still use the 16px floor, but on-page that's ~47px against ~60px for the wide panels. | — | Visible now that panels aren't cropped. `page_010` (224px-wide panels) gets 1–3 words per line at the 16px floor; one thought cloud's text overruns its outline. | `text.*_font_ratio` and `max_bubble_width` (42% of W) scale with panel **width**, so tall narrow panels get the smallest text. See W3.5. |
| 13 | **Text lines collide inside a bubble** — **FIXED (W3.6), verified 2026-10-30** on golden `page_4_panel_1` and the `page_1_panel_2` thought cloud. | — | Golden `page_4_panel_1`: "So there / are more / of you?" draws "are more" and "of you?" on top of each other. | Lines advance by each line's own glyph-ink height (`_measure`), not the font's line height. A line without descenders is "short", so the next one rides up into it. Worst at small sizes. See W3.6. |
| 14 | **Face detector misses stylised faces, accepts junk** | — | First golden run, `page_6_panel_1` (not in the current subset): Gerald's grinning face is missed; the only hit is a 0.53-score box on the floor. | The hybrid YuNet + MediaPipe threshold is 0.5. Everything face-driven inherits this (tail anchors, problem 4). The character sheet only head-crops on ≥ 0.6. |
| 15 | Thought-bubble trailing dots are tiny and bunched | — | Golden `page_1_panel_2`: the three dots sit within ~20px of the cloud, reading as one blob. | They share the speech-tail length cap (`tail_frac` × short edge), so they span only ~22px on a 448-wide panel. Not in W3's scope; noted for later polish. |

---

## Workstreams (priority order)

### W1 — Fix the generation contract (dimensions + reference routing). COMPLETE, BUT WON'T BE FULLY VERIFIED UNTIL e2e RUN.

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
(it now asserts aspect). That run takes over an hour since problem 11 and
wipes `test_e2e_output/`, so run it at a convenient moment rather than per
change; the golden subset is the per-change check.

### W2 — A fast, honest verification loop. COMPLETE AND VERIFIED.

The full 33-panel e2e run took ~30 min in June and over an hour since W1
(problem 11). Apart from W1's new aspect assertion, nothing in it checks
*quality*. Build (spec → as built):

1. **Golden subset fixture.** 2–3 pages cut from `story.cbml` that cover the
   hard cases: tall-narrow slots (0.22, 0.44), a Gerald-solo panel, a
   2-speaker panel with 4 lines (`page_3_panel_1`), a 4-character panel
   (`page_8_panel_4`). Target: <8 min per iteration.
   → `tests/fixtures/integration/golden.cbml`: story page blocks 3, 8 (a
   2-page spread) and 10, the only three that cover every listed case. 12
   panels, renumbered `page_1_*` (story 3), `page_2_*` (story 8–9) and
   `page_4_*` (story 10). Verified to enrich identically to the same panels
   in the full story. Frozen LLM-refined prompts from the June run are in
   `golden_prompts/`, re-keyed, so runs are comparable.
2. **Contact sheets** (`tools/contact_sheet.py`):
   - *Per page:* the raw panel, the panel with text, and the slot-cropped
     result side by side, so cropping damage is visible at a glance.
   - *Per character:* the reference image next to a crop of that character
     from every panel they appear in. This is the consistency yardstick for
     W4.
   → `tools/contact_sheet.py <project_dir>` writes `sheet_pages.png` (per
   page: raw | lettered | as placed on the page, with the assembled page
   alongside) and `sheet_characters.png`. Logic is in
   `src/lazycomics/review.py` (unit-tested). "As placed" uses
   `<project>/panel_slots.json`, a new assembler record of each panel's
   visible window.
3. **Machine-checkable assertions** in the e2e test: panel aspect vs request
   (W1.4), no bubble rect outside the visible slot after cropping, and every
   tail anchor inside a detected face box.
   → All three are in `test_integration_e2e.py`, via `review.check_panel`
   kinds `slot` and `tail_anchor`. The tail check is true by construction
   today, so it's a regression guard. The slot check was validated on the
   June panels: 36 cut-off boxes flagged, e.g. all three lines of story
   `page_2_panel_2`. Not yet exercised by a real e2e run (that's W1's
   pending run). Needs `text_renderer`'s new `panels_text/<id>.layout.json`.

**Additions beyond the spec** (each serves a later workstream that names it):
- `review.check_panel` also reports reading order, overlaps, covered faces
  and missing tails (W3's done-when).
- `golden_run.py --no-generate` re-letters and re-checks without the GPU
  (W3's loop).
- `--baseline` adds a before/after column (W4's experiments).

**Done when:** one command regenerates the golden subset and writes both
contact sheets. → Met 2026-10-30. `python tools/golden_run.py --force`,
starting from no `golden_output/`, generated all 12 panels and wrote
`report.txt`, `sheet_pages.png` and `sheet_characters.png`.
- **Time:** generation took 582s (19–92s per panel), so the **<8 min target
  is missed** by ~2 min. The no-GPU re-check loop takes 10s.
- **Speed levers** (not applied): `--base-resolution 768`, or problem 11's
  ref levers. Decide whether to accept ~10 min or revisit the target.
- **Checks:** 12/12 panels clean. The visible problems (problems 4, 5, 8,
  12, 13) are the kind the checks can't judge, and the sheets show them.
- **Determinism:** generation is bit-identical across runs, which is what
  makes the A/B comparisons valid.

### W3 — Bubble layout correctness

Iterate with `tools/golden_run.py --no-generate`: it re-letters and re-checks
the golden panels in seconds, no GPU.

1. **Reading order by construction (problem 3).** Replace greedy per-line
   placement with two phases. (a) Allocate vertical bands top to bottom in
   CBML line order, with a Z-path across columns when two speakers alternate.
   (b) Position each bubble horizontally inside its band near its speaker,
   using the existing cost terms (busyness, face avoidance, tail length).
   Unit regression: the June `page_3_panel_1` layout, already pinned in
   `tests/test_review.py`. The golden baseline has no live failure (problem
   3), so also check the fix on real art: run `tools/contact_sheet.py` on
   the next full e2e output, where story `page_2_panel_2` failed in the W1
   re-run.
2. **Speaker attribution (problem 4).** Match *all* panel characters (from
   `bubble_layout` order), not just speakers, to faces. Drop faces below a
   size threshold relative to the largest face, so background extras don't
   count. When face count ≠ character count, match by nearest predicted
   region centre instead of rank, and omit the tail (fail closed) when the
   match is ambiguous. Positional matching can't fix golden
   `page_4_panel_1`, where the model mirrored the predicted order. That
   needs identity: compare each face crop to the characters' refs (e.g. CLIP
   image embeddings). Decide after trying the cheap version.
3. **Persist what was decided.** Write `detected_faces` and
   `actual_character_regions` back to the enriched JSON (the old forward
   contract) so re-renders are deterministic and inspectable.
4. Then polish: ≥3-bubble numbering (only if still needed), tail-length
   minimum stub, thought-bubble cloud sizing.
5. **Size text from panel area, not width (problem 12).** Base font size and
   `max_bubble_width` on `sqrt(W*H)` (or the panel's on-page scale) so a
   224×1024 panel doesn't get the smallest text in the book. Let bubbles in
   narrow panels use most of the width. Regression check: golden
   `page_4_*`.
6. **Line spacing from the font, not the ink (problem 13).** Advance each
   wrapped line by the font's line height (ascent + descent, or
   `font.getbbox("Ay")`), not the per-line glyph bbox. Applies to bubbles
   and captions.

**Done when:** `golden_run.py` reports no reading-order or overlap issues,
`sheet_pages.png` shows every tail on the right character, and no
narrow-panel text is over-wrapped or colliding.

**As built (2026-10-30).** All in `src/lazycomics/text_renderer.py`.
Implemented in dependency order: 6 and 5 (sizes) → 2 (who is where) → 1
(placement) → 3 → 4. Verified by re-lettering the golden baseline's panels
(`golden_w3/`, compared against `golden_output/`) and the 17 W1-run panels;
no regeneration was needed.

1. **Reading order** → `_layout_bubbles`, the spec's two phases:
   - (a) Every allowed band structure is enumerated: an alternating
     speaker standing further right *may* share the previous band (the
     Z-path), and a speaker's consecutive lines always stack.
   - (b) Band heights are chosen jointly (a DP over candidate heights near
     the speakers) and x positions greedily, using the existing cost terms.

   Order and non-overlap hold by construction. **Two cost terms were
   added**, each fixing something the panels showed:
   - Faces are kept clear by one tail length, so tails don't stab into
     faces. Without this, two existing tests failed.
   - A tailed bubble placed beyond tail reach of its speaker is penalised.
     Busyness otherwise pulled bubbles 250px away onto flat floor.

   Captions and SFX are obstacles. Results: no order or overlap issues on
   any golden or W1-run panel; story `page_2_panel_2` is fixed. The e2e
   check via `contact_sheet.py` is still to come with W1's pending run.
2. **Attribution** → `_match_characters_to_faces`, as specced:
   - all characters, not just speakers;
   - faces under 50% of the tallest dropped;
   - rank match when counts are equal, else minimum total distance;
   - fail closed when ambiguous (within 5% of panel width).

   Fixed golden `page_1_panel_1` and `page_2_panel_2`. Mirrored
   compositions can't be fixed by position; the identity decision below
   (option a) fixed one of the two.
3. **Persist** → `render_text` writes `detected_faces`,
   `actual_character_regions` (character → face box) and
   `detected_faces_for` (sha1 of the panel image) into the enriched JSON.
   - Re-renders of the same image reuse them.
   - A regenerated image is detected afresh.
   - Hand edits are honoured, including a hand-added face the detector
     missed (problem 14).
   - `enrich()` clears them.
4. **Polish:**
   - ≥3-bubble numbering: **not needed.** The 4-line golden
     `page_1_panel_1` now reads unambiguously.
   - Minimum tail stub: 12px (`_TAIL_STUB_PX`).
   - Thought-cloud sizing: the June overflow was the line-spacing bug;
     after 6 the text sits inside the cloud with room to spare, so no
     sizing change was needed. The trailing dots are a separate, smaller
     issue (problem 15).
5. **Area-based sizing** → font and wrap width scale with √(W·H). The
   ratios are the old width ratios × √(4/3), so 4:3 panels keep the size
   tuned in Phase 3. Config values and comments are updated. Captions get
   the same wrap-width rule.
6. **Line spacing** → `_line_height` (font ascent + descent) for bubbles
   and captions. SFX now record their true ink box.

Tests: 16 new in `test_text_renderer.py`. 13 of the first 14 fail against
the old renderer. The obstacle test was rewritten until it fails without
the obstacle term.

**Identity decision (W3.2: "decide after trying the cheap version").** The
cheap version left 2 of 12 golden panels mirrored. The options were (a)
OpenCV SFace, (b) CLIP (torch, ~2 GB), and (c) defer identity. **You chose
(a), 2026-10-30.**

- *Spike.* 22 faces across 9 panels, with identities checked by eye:
  5 golden panels for tuning and 4 W1-run panels held out.

  | Method | Golden faces | Held-out faces | Panels fully right | Broke a correct panel? |
  |---|---|---|---|---|
  | Position only | 9/13 | 6/9 | 6/9 | — |
  | SFace only | 11/13 | 4/9 | 6/9 | yes (2) |
  | Blend | 11/13 | 7/9 | **7/9** | **no** |

  SFace's cosine similarities on this art are weak (0.1–0.56; its
  same-person threshold on photos is 0.363), and plain crops without
  landmark alignment were near-useless (8/13). So identity is used only
  blended with position.
- *Built.* `src/lazycomics/face_identity.py`, plus a blend in
  `_match_characters_to_faces`. On panels with 2+ characters and a
  reference image, each pair's score is its SFace cosine minus 0.25 × its
  distance from the predicted centre (as a fraction of the width).
  - The weight was chosen on golden, inside the range 0.125–0.45 that keeps
    correct matches and fixes `page_4_panel_1`, then confirmed on held-out.
  - With no usable identity evidence it is exactly the position-only
    matcher, including failing closed.
  - The layout record notes which was used (`"matching"`).
- *Model.* Downloaded on first use from the OpenCV Zoo to
  `src/lazycomics/assets/models/face_recognition_sface_2021dec.onnx`
  (38.7 MB, gitignored), with SHA-256 pinned (`0ba9fbfa…4e79`). If the
  download fails, matching uses position only and says so once. To bundle a
  local copy later, commit that file and remove its `.gitignore` line.
  - **Found on the first real run:** with `SSLKEYLOGFILE` set (it is in
    this harness's environment), `ssl.create_default_context()`
    hard-crashes Windows Python (`OPENSSL_Uplink`). The downloader uses a
    plain verifying `SSLContext` instead, and sweeps up `.part` files left
    by a crash.
- *Result.* Golden `page_4_panel_1` is fixed: David's tail now points at
  David. Every tail in the golden subset is now on the right character, and
  held-out results match the spike.
- **Still wrong:**
  - Golden `page_2_panel_1`: David's tail-less "EAT IT!" sits beside
    Gerald, because identity evidence there is too weak to overrule
    position.
  - Held-out story `page_4_panel_3`: the Intern has no reference image,
    and the generator reordered the cast.

  Both are tracked under problem 4.

**Done when → met 2026-10-30** on the golden subset: no reading-order or
overlap issues, every tail on the right character, and no over-wrapped or
colliding narrow-panel text. The checks report only the designed
fail-closed `no_tail` for Gerald, who isn't in `page_4_panel_1`.
Outstanding:
- the spec's real-art reading-order check on the next full e2e run, done
  so far on the 17 W1-run panels;
- the residual attributions above.

### W4 — Character + style consistency

W1 removed much of the solo-panel drift, but not all (problem 5). The
yardstick is `golden_output/sheet_characters.png`. Run each experiment as
`golden_run.py --force --out golden_<name> --baseline golden_output` so
`sheet_pages.png` shows before/after side by side.

**Definition of "consistent enough"** (fix this before spending GPU time). On
the per-character contact sheet, a reviewer can identify each recurring
character by hair colour/style, age band, and outfit in ≥90% of crops without
reading the panel's dialogue. Add an automated score (CLIP image-embedding
similarity of character crop vs reference) only if manual review becomes the
bottleneck.

Experiment ladder, cheapest first. Run each on the golden subset and keep a
result line per experiment in this section:

1. Re-baseline after W1. **First look (golden baseline 2026-10-30):** Gerald
   is on-model in golden `page_1_panel_1`, `page_2_panel_4` and
   `page_4_panel_3`. He is off-model (white hair) in the golden
   `page_1_panel_4` close-up and in `page_2_panel_2`. He is dark-haired in
   the vampire-moment panels `page_2_panel_1/3`. Ranjeet, Yuki and David look
   on-model. Score this formally against the bar below.
2. Prompt wording. Gerald's description (`GERALD.txt`) is "young pale white
   male … hair slightly messy", with no hair colour. The LLM refinement
   **dropped "young"**: the frozen golden `page_1_panel_4` prompt says only "a pale
   white hedge fund analyst". The close-up then reads as "pale white" =
   white hair, and the age cue is gone. Two cheap levers: state hair colour
   and age explicitly in the character `.txt` files, and tell the refiner's
   system prompt to keep every character's identity descriptors verbatim.
   The frozen prompts embed the old wording, so test with `--fresh-prompts`,
   then re-freeze.
3. Reference ordering/count. Klein has no per-ref weight, so order and count
   are the only levers. Try character refs before style/location, cap refs
   per panel (dilution, and each ref costs ~1 image of tokens), and drop the
   location ref on close-ups.
4. Steps and NAG. Klein is distilled for 4 steps; try 6–8. `guidance_scale`
   is inert (problem 8), so try `NAG_scale` 2–5 to make the negative prompt
   bite. That may also stop painted-in bubbles and gibberish signage.
5. Per-panel composite reference (old §14.11): one model-sheet image holding
   exactly that panel's characters. Branch experiment.
6. Flux Dev vs Klein via `tools/profile_architectures.py`, with smaller panels
   plus heavier upscaling to keep time in check.
7. LoRA (W6) if the above can't hit the bar.

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
  isn't enough. Evidence it may be needed: problem 14.

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

## Verifying a change (reference)

| Changed | Run | Time |
|---|---|---|
| Text rendering / bubble layout | `python tools/golden_run.py --no-generate` | seconds |
| Anything upstream of generation (bridge, refs, prompts, `wan2gp` config) | `python tools/golden_run.py --force --out golden_<name> --baseline golden_output` | ~10 min (582s measured) |
| Before a release / after W1-style contract changes | `pytest tests/test_integration_e2e.py -s` (wipes `test_e2e_output/`) | > 1 h |

`golden_run.py` writes `report.txt` (`review.check_panel`: aspect, text cut
off by the slot, reading order, overlaps, faces covered, tails),
`sheet_pages.png` and `sheet_characters.png`. `tools/contact_sheet.py
<project_dir>` writes the same sheets and checks for any already-rendered
project, e.g. after a full e2e run. The checks can't judge tail
*correctness* or likeness; the sheets are for that. Panels are kept between
runs, so pass `--force` whenever generation inputs change, or stale panels
get reviewed. The subset and why each page is in it: `GOLDEN_PAGES` in the
tool.

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
