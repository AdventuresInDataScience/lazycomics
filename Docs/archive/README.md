# Archived planning docs

Superseded by [`../PLAN.md`](../PLAN.md). Kept for history only — do not
update these.

| File | What it was | Why archived |
|---|---|---|
| `2026-11-03_PLAN_W7-W8.md` | `PLAN.md` on 2026-11-03, as W7 (lettering) and W8 (generation fixes) closed. It holds their as-built records: the lettering checks and bench results, the attribution re-measure that dropped SFace, every W8 A/B (NAG, steps, group cue, signage, dialogue context) with its run name, and the 2026-11-03 rework (rule checks, refiner bench, held-out script) | Both closed on your read; the live plan keeps only open work. W8's leftovers went to the refinement backlog. |
| `2026-11-02_PLAN_W5-W6.md` | `PLAN.md` on 2026-11-02, after W6 (the LoRA training pipeline) was built: W6's as-built record (AI Toolkit compatibility check, GERALD's generated training set, VRAM defaults), and W5 closed with nothing built | The plan was restocked to put the e2e faults first and all LoRA work last (W11, on its own branch). An earlier session had reported W5 done; it wasn't, and this copy records that. |
| `2026-11-01_PLAN_W0-W4.md` | `PLAN.md` from 2026-10-30 to 2026-11-01: the W0–W4 workstreams with their as-built records (Wan2GP tracing, golden-subset design, the W3 layout rework and SFace identity spike, the W4 experiment ladder) and the problem table with its verification history | All of W0–W4 closed on 2026-11-01. The live plan restarted with only open work; this keeps the measurements and decisions behind it. Its paths predate the move of test fixtures and outputs to `tests/e2e/` (mapping in `PLAN.md`, "Verifying a change"). |
| `2026-06-10_Implementation_plan.md` | Original design (§§1–13) plus the Phase 3 patch log (§14.0–14.11) | §§1–13 describe an architecture that was never built (`comic_creator/` with `core/`/`pipeline/`/`ui/`, SDXL + IP-Adapter + ControlNet, a Gradio UI) and use a different phase numbering. §14 accreted status notes on top of the original symptom/steps text until the two contradicted each other. |
| `2026-06-10_Current_status_of_implementation_plan.md` | "Single source of truth" status tracker | Marked several issues RESOLVED that the 2026-06-10 e2e output shows are not (aspect ratio, reading order). See `PLAN.md` § "Where things actually stand". |

Code comments of the form `plan §N` or `§14.x` (in `src/lazycomics/*.py` and
`lazycomics_config.yaml`) refer to sections of the archived
`2026-06-10_Implementation_plan.md`. Comments citing W1, W3 or W4 refer to
`2026-11-01_PLAN_W0-W4.md`, W6 to `2026-11-02_PLAN_W5-W6.md`, and W7 or W8
(including "Docs/PLAN.md, W8") to `2026-11-03_PLAN_W7-W8.md`. W11 in these
files is the LoRA path, renumbered W12 on 2026-11-03 when W11 became
Refinement. The design principles and forward contracts were
carried over into `PLAN.md`.
