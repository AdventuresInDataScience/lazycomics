"""Live smoke test — runs MULTIPLE panels (a queue) through the real Wan2GP bridge.

Usage (from the repo root, with your lazycomics venv active):

    python tests/test_wan2gp_live_queue.py

What it does:
    1. Creates a throwaway project in ./test_live_output/
    2. Writes TWO enriched panel JSONs + TWO prompt text files
    3. Calls generate_panels() against your real Pinokio Wan2GP
    4. Prints the output paths so you can visually inspect the PNGs

Prerequisites:
    - lazycomics_config.yaml has correct wan2gp.wgp_root / python_bin
    - Wan2GP model weights already downloaded
    - Pinokio is NOT actively running Wan2GP
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics.config import load_config
from lazycomics.project import create_project
from lazycomics.wan2gp_bridge import generate_panels


def main():
    output_root = Path("./test_live_output_queue")

    # Clean previous run
    if output_root.exists():
        shutil.rmtree(output_root)

    # Minimal CBML
    cbml_path = output_root / "story.cbml"
    output_root.mkdir(parents=True)
    cbml_path.write_text(
        "## Live Bridge Queue Test\n"
        "aspect: 2:3\n\n"
        "PAGE preset:splash\n\n"
        "PANEL A\n"
        "loc: steampunk_alley\n"
        "> A test panel 1.\n\n"
        "PANEL B\n"
        "loc: cyberpunk_street\n"
        "> A test panel 2.\n"
    )

    project = create_project("live_queue_test", cbml_path, base_dir=output_root)

    # Write enriched JSON for panel 1
    enriched_1 = {
        "panel_id": "page_1_panel_1",
        "page_index": 0,
        "panel_index": 0,
        "aspect_ratio": 2 / 3,  # portrait
        "shot_hint": "medium shot",
        "mood": "noir",
        "characters": [],
        "primary_character": None,
        "char_generation_strategy": "single",
        "inpaint_order": [],
        "loc_identifier": "steampunk_alley",
        "loc_description": "a dark steampunk alley with yellow gaslight",
    }
    (project.enriched_dir / "page_1_panel_1.json").write_text(json.dumps(enriched_1, indent=2))

    # Write a prompt for panel 1
    (project.prompts_dir / "page_1_panel_1.txt").write_text(
        "A dark steampunk alley at night, yellow gaslight, "
        "cobblestones, pipes and gears on walls, noir atmosphere, "
        "dramatic shadows, comic book style, detailed illustration"
    )

    # Write enriched JSON for panel 2
    enriched_2 = {
        "panel_id": "page_1_panel_2",
        "page_index": 0,
        "panel_index": 1,
        "aspect_ratio": 1.5,  # landscape
        "shot_hint": "wide shot",
        "mood": "cyberpunk",
        "characters": [],
        "primary_character": None,
        "char_generation_strategy": "single",
        "inpaint_order": [],
        "loc_identifier": "cyberpunk_street",
        "loc_description": "a vibrant neon cyberpunk city street in the rain",
    }
    (project.enriched_dir / "page_1_panel_2.json").write_text(json.dumps(enriched_2, indent=2))

    # Write a prompt for panel 2
    (project.prompts_dir / "page_1_panel_2.txt").write_text(
        "A vibrant neon cyberpunk city street in the rain, flying cars, "
        "holographic billboards, wet reflective pavement, cinematic lighting, "
        "comic book style, wide angle"
    )


    # Load config (picks up wan2gp paths from lazycomics_config.yaml)
    config = load_config()
    
    # Disable image requirement for pure text test
    config["wan2gp"]["video_prompt_type"] = "T"

    print("=" * 60)
    print("LIVE WAN2GP QUEUE BRIDGE TEST")
    print("=" * 60)
    print(f"Project dir: {project.base_dir}")
    print()
    print("Calling generate_panels() for multiple panels...")
    print()

    try:
        results = generate_panels(project, force=True, config=config)
        
        print("\n" + "=" * 60)
        out1 = project.panels_dir / "page_1_panel_1.png"
        out2 = project.panels_dir / "page_1_panel_2.png"
        
        if out1.is_file() and out2.is_file():
            print(f"SUCCESS — multiple images generated!")
            print(f"Panel 1: {out1}")
            print(f"Panel 2: {out2}")
        else:
            print("FAILED — Not all output panels exist.")
            if not out1.is_file(): print(f"Missing: {out1}")
            if not out2.is_file(): print(f"Missing: {out2}")
            sys.exit(1)
        print("=" * 60)
        
    except Exception as e:
        print(f"\nFAILED: Exception occurred: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()