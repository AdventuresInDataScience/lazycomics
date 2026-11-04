# lazycomics

Composable helpers for turning CBML scripts into `.cbz` comic books.

Read a [CBML](https://github.com/AdventuresInDataScience/cbml_parser) script
that describes a comic — pages, panels, characters, dialogue, sound
effects — and produce a `.cbz` you can open in any comic viewer.

The pipeline is **staged**: each step writes intermediates (enriched
JSON, prompts, prepared refs, rendered panels, assembled pages) so you
can review and adjust between steps. The heavy AI work (panel
generation, LoRA training) lives in external services (Wan2GP, AI
Toolkit); this package is the orchestration layer. Upscaling runs an ONNX
model in-process.

## Status

The full pipeline runs end-to-end: CBML → Wan2GP-generated panels (Flux 2
Klein) → upscaled 4x → lettered pages → `.cbz`, plus print-size pages. Characters stay consistent across the test
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

Optional extras:

```bash
pip install lazycomics[llm]      # LLM-assisted prompt refinement (requests)
pip install lazycomics[upscale]  # panel upscaling (onnxruntime; see "Upscaling")
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

# 7b. Panels → 4x upscaled copies, so lettering and pages are drawn at full
#     size (needs `upscaler.model_path`; see "Upscaling"). Skip it and the
#     panels are lettered as generated.
cc.upscale_panels(project)

# 7c. Panel PNGs + dialogue/SFX → panels with text overlays
cc.render_text(project)

# 8. Panels → per-page composite PNGs (pages/), plus print-size copies
#    (pages_print/)
cc.assemble_pages(project)

# 9. Pages → .cbz archive
output = cc.export_cbz(project)
print(f"Done: {output}")
```

A runnable demo with placeholder data is in
[`examples/basic_usage.py`](examples/basic_usage.py).

## Writing descriptions

What the image model is told decides what it draws:

- **Characters:** state an age band, hair colour and style, and an outfit
  that match the reference image. Loosely described characters drift
  between panels.
- **Signs, posters and banners:** describe them by shape and light
  ("abstract neon light shapes"), never as signs. The model fills any sign
  with gibberish lettering. `build_prompts` warns when a location or a shot
  mentions one.
- **Who stands where:** list a panel's `chars:` left to right as you want
  them drawn: the lettering points each speaker's tail on that assumption.
  Panels with three or more characters, two or more of whom speak, get a
  "four people, left to right: …" cue in that order.
- **Dialogue and sound effects** are never put in the prompt (the model
  would paint them in), but the LLM refiner reads them to draw the action
  they imply, such as a character writing under a "SCRIBBLE" effect.

## Configuration

Optional `lazycomics_config.yaml` in the current directory (or your
home directory) supplies defaults. Function-call args always win;
config supplies project-wide defaults; hardcoded defaults are the
final fallback.

```yaml
llm:
  url: http://127.0.0.1:11434/v1   # Ollama; "localhost" costs ~2s a request on Windows
  model: llama3.1:8b
  think: false                      # thinking models: skip the reasoning (~7x faster)
  api_key:                          # optional, e.g. for OpenAI
  system_prompt: |
    Optional custom system prompt for prompt refinement.

assembly:
  page_height_px: 3000          # the pages the .cbz ships
  print_page_height_px: 6000    # pages_print/: ~600 DPI at US comic size; 0 = off
  gutter_px: 20
  bg_color: "#ffffff"
  stretch_tolerance: 0.0
```

## Upscaling

Panels are generated at 1024px on the long edge, but a 3000px page needs up
to ~3x that to fill its slots, and a 6000px print page ~6x. Without an
upscaler, assembly stretches them with Lanczos, lettering included.
`upscale_panels` enlarges each panel 4x first (`panels/` →
`panels_upscaled/`). `render_text` then letters the upscaled copy, still
laying out on the generated panel, so the lettering is the same, only
sharper. Assembly only ever shrinks.

The model is [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN)'s
`realesrgan-x4plus-anime` (BSD-3), exported to ONNX and run with
`onnxruntime`. No torch or CUDA is needed: on Windows, `onnxruntime-directml`
uses any GPU. It was picked against IllustrationJaNai, WTP-UDS and
AnimeSharpV3 on the test story's panels ([`Docs/PLAN.md`](Docs/PLAN.md),
Reference, "Upscaler"). Any 4x ESRGAN-family model exported the same way
can be swapped in through `upscaler.model_path`.

**Install, once:**

1. `pip install lazycomics[upscale]` (or `uv pip install onnxruntime-directml`
   into an existing env).
2. Download
   [`RealESRGAN_x4plus_anime_6B.pth`](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth)
   (18 MB).
3. Export it to ONNX. This needs a Python with `torch`, `onnx` and
   `onnxruntime`; Wan2GP's Pinokio env has them:

   ```powershell
   C:\pinokio\api\wan.git\app\env\Scripts\python.exe tools\export_upscaler_onnx.py RealESRGAN_x4plus_anime_6B.pth RealESRGAN_x4plus_anime_6B.onnx
   ```

   It checks the export against torch before it exits.
4. Point `upscaler.model_path` in `lazycomics_config.yaml` at the `.onnx`.

**Time**, on an RTX 3080 Laptop with DirectML, on AC power: 2.7s a panel
including the PNG write, about 9s per page of the test story (33 panels, 10
pages). Lettering at 4x adds about 2.4s a page, and the print pages another
2.4s. On the CPU expect minutes per panel. The upscaled panels and
lettered copies take about 6 MB each on disk.

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
├── panels_upscaled/     # the same panels, 4x (upscale_panels)
├── panels_text/         # panels with text/bubbles/SFX overlaid
├── pages/               # composited page PNGs: what the .cbz ships
├── pages_print/         # the same pages at print size
└── output/              # final .cbz lives here
```

## Development

```
tests/
├── test_*.py                 unit tests: fast, no GPU
├── e2e/                      the end-to-end test only: full story → .cbz, with checks
├── smoke/                    one_panel.py, two_panels.py: real Wan2GP, minutes
├── fixtures/                 inputs (tracked)
│   ├── main_story/           the test comic
│   │   ├── story.cbml        full story (33 panels): the e2e test
│   │   ├── subset.cbml       12 panels cut from it: tools/ab_run.py
│   │   ├── subset_prompts/   frozen prompts for the subset, so A/B runs are comparable
│   │   ├── characters/, locations/   <ID>.txt description + optional <ID>.png ref
│   │   └── Style.txt / .png
│   ├── holdout_story/        a second, 7-panel script laid out the same way, with its
│   │                         own cast, setting and style: checks changes beyond main_story
│   └── face_labels.json      hand labels: who is where, for scoring attribution
├── outputs/                  gitignored; one folder per test or tool, each run replaces its own
│   ├── e2e/                  the e2e test's project
│   ├── smoke/                one_panel/, two_panels/
│   ├── ab_runs/<name>/       tools/ab_run.py --out <name>
│   ├── lettering_bench/      holdout/, synthetic/: its re-lettered copies
│   ├── refiner_bench/        its scripts and prompts
│   └── reviews/<name>/       one-off comparison sheets for review
└── archive/                  gitignored; runs kept on purpose, same layout as outputs/
    ├── e2e/<date>[_label]/
    └── ab_runs/<date>_<name>/
tools/                        ab_run.py, contact_sheet.py, lettering_bench.py,
                              refiner_bench.py, train_lora.py, profile_architectures.py,
                              export_upscaler_onnx.py
```

```bash
pytest tests --ignore=tests/e2e                     # unit tests, ~20s
python tests/smoke/two_panels.py                    # Wan2GP bridge smoke test, minutes
python tools/ab_run.py --force --out NAME --baseline w10_refs_max4    # A/B run of the subset, ~8 min
python tools/ab_run.py --story tests/fixtures/holdout_story --refine --force --out NAME
                                                    # the held-out script, ~8 min
python tools/refiner_bench.py                       # refiner rules on synthetic + held-out scripts, LLM only, ~9 min
pytest tests/e2e/test_integration_e2e.py -v -s      # full e2e, ~30 min on AC
python tools/contact_sheet.py tests/outputs/e2e [--reletter]   # sheets + checks for any project
python tools/lettering_bench.py                     # lettering checks on tuning, held-out + synthetic panels, ~2.5 min
python tools/train_lora.py dataset|train PROJECT_DIR CHARACTER   # LoRA training set, then LoRA
```

To keep a run, move it from `tests/outputs/<kind>/` to
`tests/archive/<kind>/`, date-prefixed, before re-running; then rewrite the
old folder path in its `enriched/*.json` (projects store absolute asset
paths). Which check to run for which change:
[`Docs/PLAN.md`](Docs/PLAN.md), "Verifying a change".

## License

MIT.
