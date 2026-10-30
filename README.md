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
Klein) → lettered pages → `.cbz`. Output quality is the current focus
(character consistency, bubble layout). LoRA training is not built yet.

See [`Docs/PLAN.md`](Docs/PLAN.md) for what works, what doesn't, and
what's next.

## Installation

```bash
pip install lazycomics
```

Requires **Python 3.10 – 3.12**. (MediaPipe doesn't ship 3.13 wheels yet.)

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
│   ├── characters/<NAME>/{meta.json, references/, lora.safetensors}
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

## License

MIT.
