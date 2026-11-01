"""Live smoke test — runs ONE panel through the real Wan2GP bridge.

Usage (from the repo root, with your lazycomics venv active):

    python tests/test_wan2gp_live.py

What it does:
    1. Creates a throwaway project in ./test_live_output/
    2. Writes one enriched panel JSON + one prompt text file
    3. Calls generate_panels() against your real Pinokio Wan2GP
    4. Prints the output path so you can visually inspect the PNG

Prerequisites:
    - lazycomics_config.yaml has correct wan2gp.wgp_root / python_bin
    - Wan2GP model weights already downloaded (first run in the UI)
    - Pinokio is NOT actively running Wan2GP (avoids GPU contention)

Takes ~30-60s depending on your GPU. The output lands in:
    ./test_live_output/panels/page_1_panel_1.png
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
    output_root = Path("./test_live_output")

    # Clean previous run
    if output_root.exists():
        shutil.rmtree(output_root)

    # Minimal CBML
    cbml_path = output_root / "story.cbml"
    output_root.mkdir(parents=True)
    cbml_path.write_text(
        "## Live Bridge Test\n"
        "aspect: 2:3\n\n"
        "PAGE preset:splash\n\n"
        "PANEL A\n"
        "loc: steampunk_alley\n"
        "> A test panel.\n"
    )

    project = create_project("live_test", cbml_path, base_dir=output_root)

    # Write enriched JSON for one panel
    enriched = {
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
    (project.enriched_dir / "page_1_panel_1.json").write_text(
        json.dumps(enriched, indent=2)
    )

    # Write a prompt (no ref image — pure text-to-image for simplicity)
    (project.prompts_dir / "page_1_panel_1.txt").write_text(
        "A dark steampunk alley at night, yellow gaslight, "
        "cobblestones, pipes and gears on walls, noir atmosphere, "
        "dramatic shadows, comic book style, detailed illustration"
    )

    # Load config (picks up wan2gp paths from lazycomics_config.yaml)
    config = load_config()
    
    # Disable image requirement for pure text test
    config["wan2gp"]["video_prompt_type"] = "T"

    print("=" * 60)
    print("LIVE WAN2GP BRIDGE TEST")
    print("=" * 60)
    print(f"Project dir: {project.base_dir}")
    print(f"Panel: page_1_panel_1 (portrait 2:3)")
    print()
    print("Calling generate_panels()...")
    print()

    try:
        results = generate_panels(project, force=True, config=config)
    except RuntimeError as e:
        print(f"\nFAILED: {e}")
        print("\nCheck that:")
        print("  1. wan2gp.wgp_root and python_bin are correct in config")
        print("  2. Wan2GP model weights are downloaded")
        print("  3. Pinokio is not running Wan2GP (GPU contention)")
        sys.exit(1)

    if "page_1_panel_1" in results:
        out_path = results["page_1_panel_1"]
        size = out_path.stat().st_size
        print()
        print("=" * 60)
        print(f"SUCCESS — image at: {out_path}")
        print(f"File size: {size:,} bytes")
        print("=" * 60)
        print()
        print("Open this file to visually inspect the generated panel.")
    else:
        print("\nNo image was returned. Check the Wan2GP output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
