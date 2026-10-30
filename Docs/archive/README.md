# Archived planning docs

Superseded by [`../PLAN.md`](../PLAN.md) on 2026-10-30. Kept for history only —
do not update these.

| File | What it was | Why archived |
|---|---|---|
| `2026-06-10_Implementation_plan.md` | Original design (§§1–13) plus the Phase 3 patch log (§14.0–14.11) | §§1–13 describe an architecture that was never built (`comic_creator/` with `core/`/`pipeline/`/`ui/`, SDXL + IP-Adapter + ControlNet, a Gradio UI) and use a different phase numbering. §14 accreted status notes on top of the original symptom/steps text until the two contradicted each other. |
| `2026-06-10_Current_status_of_implementation_plan.md` | "Single source of truth" status tracker | Marked several issues RESOLVED that the 2026-06-10 e2e output shows are not (aspect ratio, reading order). See `PLAN.md` § "Where things actually stand". |

Code comments of the form `plan §N` or `§14.x` (in `src/lazycomics/*.py` and
`lazycomics_config.yaml`) refer to sections of the archived
`2026-06-10_Implementation_plan.md`. The design principles and forward
contracts from the status doc were carried over into `PLAN.md`.
