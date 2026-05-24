"""Real-ESRGAN upscaler (Phase 2 — post-assembly upscaling).

Upscales assembled pages from ``pages/`` to ``pages_upscaled/`` using
the ``realesrgan-ncnn-vulkan`` binary. This is a portable C++ binary
that uses Vulkan for GPU acceleration — no Python/CUDA/torch required.

Pipeline position::

    pages/*.png  →  upscaler.upscale_pages()  →  pages_upscaled/*.png

Installation
~~~~~~~~~~~~

Download the binary from:
    https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan/releases

Extract somewhere and set ``upscaler.executable`` in config to the path.

Configuration
~~~~~~~~~~~~~

In ``lazycomics_config.yaml``::

    upscaler:
      executable: realesrgan-ncnn-vulkan   # or full path to the binary
      model: realesrgan-x4plus-anime       # anime-optimised 4x (best for comics)
      scale: 4                             # fixed by model: 4 for x4plus, 2-4 for animevideov3

Resolution note
~~~~~~~~~~~~~~~

The ``wan2gp.base_resolution`` config controls the **generation**
resolution (long edge of each panel before assembly). The final output
resolution is determined by the page assembly grid multiplied by this
upscaler's scale factor.

For example: ``base_resolution: 1024`` with a 2×3 grid and 4× upscale
produces pages roughly 8192×12288 pixels — print-ready at 300 DPI.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from lazycomics.config import cfg_get, load_config
from lazycomics.models import Project

__all__ = ["upscale_pages"]


def upscale_pages(
    project: Project,
    *,
    force: bool = False,
    config: dict[str, Any] | None = None,
) -> list[Path]:
    """Upscale assembled pages via Real-ESRGAN.

    Reads from ``<project>/pages/``, writes to ``<project>/pages_upscaled/``.

    Parameters
    ----------
    project
        The lazycomics project.
    force
        If ``False`` (default), skips entirely when all pages already
        have an upscaled counterpart. If ``True``, regenerates all.
    config
        Loaded config dict; if ``None``, calls ``load_config()`` internally.

    Returns
    -------
    list[Path]
        Paths to the upscaled page images.
    """
    if config is None:
        config = load_config()

    ucfg = _load_upscaler_config(config)

    # Discover source pages
    source_pages = sorted(
        p for p in project.pages_dir.iterdir()
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        and p.is_file()
    )
    if not source_pages:
        print("[upscaler] no pages found in pages/")
        return []

    # Check if we can skip
    if not force:
        expected = [project.pages_upscaled_dir / p.name for p in source_pages]
        if all(e.is_file() for e in expected):
            print("[upscaler] all pages already upscaled, skipping (use force=True to redo)")
            return expected

    project.pages_upscaled_dir.mkdir(parents=True, exist_ok=True)

    _run_realesrgan(
        input_dir=project.pages_dir,
        output_dir=project.pages_upscaled_dir,
        ucfg=ucfg,
    )

    results = sorted(
        p for p in project.pages_upscaled_dir.iterdir()
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        and p.is_file()
    )

    print(f"[upscaler] {len(results)} pages upscaled ({ucfg['model']}, {ucfg['scale']}x)")
    return results


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_upscaler_config(config: dict[str, Any]) -> dict[str, Any]:
    """Extract the ``upscaler`` section with defaults."""
    executable = cfg_get(config, "upscaler.executable", "realesrgan-ncnn-vulkan")

    # Resolve: if it's a bare name, check PATH; if a path, expand it.
    exe_path = shutil.which(executable) or executable

    return {
        "executable": exe_path,
        "model": cfg_get(config, "upscaler.model", "realesrgan-x4plus-anime"),
        "scale": int(cfg_get(config, "upscaler.scale", 4)),
    }


def _run_realesrgan(
    input_dir: Path,
    output_dir: Path,
    ucfg: dict[str, Any],
) -> None:
    """Shell out to realesrgan-ncnn-vulkan with directory input/output."""
    cmd = [
        ucfg["executable"],
        "-i", str(input_dir),
        "-o", str(output_dir),
        "-n", ucfg["model"],
        "-s", str(ucfg["scale"]),
    ]

    print(f"[upscaler] running: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.stdout:
        for line in result.stdout.strip().splitlines():
            print(f"[upscaler] {line}")

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "(no stderr)"
        raise RuntimeError(
            f"Real-ESRGAN exited with code {result.returncode}:\n{stderr}"
        )
