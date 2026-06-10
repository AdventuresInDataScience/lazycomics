#!/usr/bin/env python3
"""CLI: profile Wan2GP architectures over a project's panels.

Usage::

    python tools/profile_architectures.py <project_dir> [arch ...]

Compares generation time for each architecture (default: just
``flux2_klein_9b``; pass a Flux Dev ``model_type`` as a second arg to compare,
e.g. ``flux``). Requires a working Wan2GP install — set ``wan2gp.wgp_root`` and
``wan2gp.python_bin`` in ``lazycomics_config.yaml`` — and actually generates
panels, so run it on the machine with the GPU.

Prerequisite: ``<project_dir>`` must already have been through
``enrich -> build_prompts -> prepare_references`` so the bridge has enriched
JSON, prompts, and prepared refs to work from. To benchmark a fresh 6-panel
page, create a project from ``tools/fixtures/profile_6panel.cbml`` and run those
steps first.
"""

import sys
from pathlib import Path

# Allow running straight from a source checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lazycomics.profiling import format_profile, profile_architectures  # noqa: E402
from lazycomics.project import load_project  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    project = load_project(argv[1])
    architectures = argv[2:] or ["flux2_klein_9b"]
    print(
        f"Profiling {len(architectures)} architecture(s) over "
        f"'{project.name}': {', '.join(architectures)}\n",
        flush=True,
    )
    results = profile_architectures(project, architectures)
    print(format_profile(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
