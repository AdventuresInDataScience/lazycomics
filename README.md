# lazycomics

Composable helpers for turning CBML scripts into `.cbz` comic books.

Read a [CBML](https://github.com/AdventuresInDataScience/cbml_parser) script
that describes a comic — pages, panels, characters, dialogue, sound
effects — and produce a `.cbz` you can open in any comic viewer.

The pipeline is **staged**: each step writes intermediates (enriched
JSON, prompts, prepared refs, rendered panels, assembled pages) so you
can review and adjust between steps. The heavy AI work (panel
generation, LoRA training, upscaling) lives in external services
(Wan2GP, AI Toolkit, Real-ESRGAN); this package is the orchestration
layer.

## Status

The full pipeline runs end-to-end: CBML → Wan2GP-generated panels (Flux 2
Klein) → lettered pages → `.cbz`. Characters stay consistent across the test
story's 33 panels, from reference images and descriptions alone. Next:
lettering and generation polish. Per-character LoRA training (AI Toolkit) is
built but parked: the pipeline aims for consistency without LoRAs, to avoid
the training cost.

See [`Docs/PLAN.md`](Docs/PLAN.md) for what works, what doesn't, and
what's next.

## Installation

```bash
pip install lazycomics
```

Requires **Python 3.10 – 3.12**. (MediaPipe doesn't ship 3.13 wheels yet.)

The first time text is rendered on a multi-character panel, lazycomics
downloads OpenCV's SFace face-recognition model (~37 MB, checksum-verified)
into `src/lazycomics/assets/models/`. It's used to work out which character
a face belongs to. Offline, rendering still works and matches speakers by
position only.

Optional extras:

```bash
pip install lazycomics[llm]    # LLM-assisted prompt refinement (requests)
```

## Quickstart

```python
import lazycomics as cc

# 1. Project
project = cc.create_project("my_comic", "story.cbml")

# 2. Register characters / style / locations (once per project)
cc.register_character(
    project, "NOVA",
    description="young woman, blue hair, leather jacket",
    reference_images=["nova_face.png"],
)
cc.register_style(
    project,
    prompt_prefix="silver age comic art, bold inks",
    reference_images=["style_ref.png"],
)

# 2b. (Optional) Train a character LoRA (needs the `wan2gp:` and
#     `ai_toolkit:` paths in lazycomics_config.yaml). Wan2GP draws the
#     character in varied shots; hand-pick, then AI Toolkit trains and the
#     LoRA is attached to the character. Or: tools/train_lora.py.
# cc.generate_training_set(project, "NOVA")
# cc.train_character_lora(project, "NOVA")

# 3. CBML → enriched per-panel JSON
cc.enrich(project)

# 4. Enriched JSON → prompt .txt files
cc.build_prompts(project)

# 5. (Optional) LLM-assisted prompt refinement
# cc.refine_prompts_with_llm(project)

# 6. Source images → aspect-cropped reference stacks
cc.prepare_references(project)

# 7a. Prompts + refs → panel PNGs via Wan2GP (needs the `wan2gp:` paths
#     in lazycomics_config.yaml). Or drop your own PNGs into
#     <project>/panels/<panel_id>.png.
cc.generate_panels(project)

# 7. Panel PNGs + dialogue/SFX → panels with text overlays
cc.render_text(project)

# 8. Panels → per-page composite PNGs
cc.assemble_pages(project)

# 9. Pages → .cbz archive
output = cc.export_cbz(project)
print(f"Done: {output}")
```

A runnable demo with placeholder data is in
[`examples/basic_usage.py`](examples/basic_usage.py).

## Configuration

Optional `lazycomics_config.yaml` in the current directory (or your
home directory) supplies defaults. Function-call args always win;
config supplies project-wide defaults; hardcoded defaults are the
final fallback.

```yaml
llm:
  url: http://localhost:11434/v1   # Ollama default
  model: llama3.1:8b
  api_key:                          # optional, e.g. for OpenAI
  system_prompt: |
    Optional custom system prompt for prompt refinement.

assembly:
  page_height_px: 3000
  gutter_px: 20
  bg_color: "#ffffff"
  stretch_tolerance: 0.0
```

## Project layout

`create_project("my_comic", "story.cbml")` creates:

```
projects/my_comic/
├── source.cbml          # copy of the input
├── manifest.json        # {"name": "my_comic"}
├── assets/              # registered characters / style / locations
│   ├── characters/<NAME>/{meta.json, references/, lora_dataset/, lora/}
│   ├── style/{meta.json, references/, lora.safetensors}
│   └── locations/<NAME>/{meta.json, references/}
├── enriched/            # one .json per panel
├── prompts/             # <panel_id>.txt + .neg.txt
├── refs_prepared/       # <panel_id>_ref.png + _ref_N.png
├── panels/              # generated panels (Wan2GP output goes here)
├── panels_text/         # panels with text/bubbles/SFX overlaid
├── pages/               # composited page PNGs
├── pages_upscaled/      # Real-ESRGAN output (Phase 2)
└── output/              # final .cbz lives here
```

## Development

```
tests/
├── test_*.py                 unit tests: fast, no GPU
└── e2e/                      the real pipeline (Wan2GP, Ollama): needs the GPU
    ├── test_integration_e2e.py   full story → .cbz, with checks
    ├── test_wan2gp_live*.py      1- and 2-panel bridge smoke tests
    ├── fixtures/                 the test comic:
    │   ├── story.cbml            full story (33 panels), used by the e2e test
    │   ├── golden.cbml           12-panel subset, used by tools/golden_run.py
    │   ├── <NAME>.txt / .png     character, location and style descriptions + refs
    │   └── golden_prompts/       frozen prompts, so golden runs are comparable
    ├── outputs/                  gitignored; each run replaces its own folder
    │   ├── e2e/                  test_integration_e2e.py's project
    │   ├── golden/<name>/        tools/golden_run.py --out <name>
    │   └── live/, live_queue/    the smoke tests
    └── archived_test_outputs/    gitignored; old runs kept on purpose, date-prefixed
tools/                        golden_run.py, contact_sheet.py, train_lora.py,
                              profile_architectures.py
```

```bash
pytest tests --ignore=tests/e2e                     # unit tests, ~1 min
python tests/e2e/test_wan2gp_live_queue.py          # bridge smoke test, minutes
python tools/golden_run.py --force --out NAME --baseline w4_prompts   # golden subset, ~10 min
pytest tests/e2e/test_integration_e2e.py -v -s      # full e2e, 30+ min
python tools/contact_sheet.py tests/e2e/outputs/e2e # sheets + checks for any project
python tools/train_lora.py dataset|train PROJECT_DIR CHARACTER   # LoRA training set, then LoRA
```

To keep a run, move its folder into `tests/e2e/archived_test_outputs/`
(date-prefixed) before re-running. Which check to run for which change:
[`Docs/PLAN.md`](Docs/PLAN.md), "Verifying a change".

## License

MIT.
