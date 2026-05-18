"""Wan2GP generation bridge (Phase 2 — panel generation).

Calls Wan2GP headlessly via ``--process queue.zip`` inside the
Pinokio-managed Python environment. No Gradio, no server — just a
subprocess into the correct env running the CLI in headless mode.

Pipeline position::

    enriched/*.json + prompts/*.txt + refs_prepared/*.png
        ↓
    wan2gp_bridge.generate_panels()
        ↓
    panels/<panel_id>.png

The bridge reads enriched JSON, prompts, and prepared refs, builds a
Wan2GP queue.zip, shells out to ``wgp.py --process``, and copies the
generated images back to ``panels/``.

Configuration
~~~~~~~~~~~~~

Required in ``lazycomics_config.yaml`` under the ``wan2gp`` key::

    wan2gp:
      wgp_root: C:\\pinokio\\api\\wan.git\\app
      python_bin: C:\pinokio\api\wan.git\app\env\Scripts\python.exe
      architecture: flux2_klein_9b
      default_steps: 4
      base_resolution: 1024
      seed: null            # null = random per panel
      cli_args: []          # extra CLI flags, e.g. ["--attention", "sdpa"]

``wgp_root`` and ``python_bin`` have no defaults — they depend on your
Pinokio install path and must be set explicitly.

Task dictionary
~~~~~~~~~~~~~~~

The ``_build_wangp_task`` function constructs the settings dict that
Wan2GP expects. The field names below are derived from the Wan2GP
``defaults/*.json`` format and ``wgp.py`` source (``image_names_list``).
If your Wan2GP version uses different keys, update ``_build_wangp_task``
— it is the sole adapter between lazycomics and Wan2GP.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from lazycomics.config import cfg_get, load_config
from lazycomics.models import Project

__all__ = ["generate_panels"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_panels(
    project: Project,
    panels: list[str] | None = None,
    *,
    force: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Generate panel images via Wan2GP.

    Reads ``<project>/enriched/*.json``, ``<project>/prompts/<id>.txt``,
    and ``<project>/refs_prepared/<id>_ref*.png``. Writes generated images
    to ``<project>/panels/<panel_id>.png``.

    Parameters
    ----------
    project
        The lazycomics project.
    panels
        Panel IDs to generate; ``None`` processes every enriched panel.
    force
        If ``False`` (default), panels whose output already exists in
        ``panels/`` are skipped. If ``True``, all panels are regenerated.
    config
        Loaded config dict; if ``None``, calls ``load_config()`` internally.

    Returns
    -------
    dict[str, Path]
        ``{panel_id: path_to_generated_png}`` for each panel that was
        generated (or already existed when skipped).
    """
    if config is None:
        config = load_config()

    bridge_cfg = _load_bridge_config(config)

    if panels is None:
        panel_ids = sorted(p.stem for p in project.enriched_dir.glob("*.json"))
    else:
        panel_ids = list(panels)

    # Partition into skip / generate
    results: dict[str, Path] = {}
    to_generate: list[str] = []

    for pid in panel_ids:
        out = project.panels_dir / f"{pid}.png"
        if out.is_file() and not force:
            results[pid] = out
        else:
            to_generate.append(pid)

    if not to_generate:
        return results

    # Build task list
    tasks = []
    task_panel_ids: list[str] = []
    for pid in to_generate:
        enriched_path = project.enriched_dir / f"{pid}.json"
        if not enriched_path.is_file():
            print(f"[wan2gp] skipping {pid}: no enriched JSON")
            continue

        panel = json.loads(enriched_path.read_text(encoding="utf-8"))
        prompt = _read_prompt(project.prompts_dir, pid)
        neg_prompt = _read_prompt(project.prompts_dir, pid, negative=True)
        refs = _collect_refs(project.refs_prepared_dir, pid)
        resolution = _compute_resolution(
            float(panel.get("aspect_ratio") or 1.0),
            bridge_cfg["base_resolution"],
        )

        task = _build_wangp_task(
            panel_data=panel,
            prompt=prompt,
            negative_prompt=neg_prompt,
            refs=refs,
            resolution=resolution,
            bridge_cfg=bridge_cfg,
        )
        tasks.append(task)
        task_panel_ids.append(pid)

    if not tasks:
        return results

    # Ensure panels/ exists
    project.panels_dir.mkdir(parents=True, exist_ok=True)

    # Build queue.zip, run Wan2GP, collect outputs
    with tempfile.TemporaryDirectory(prefix="lazycomics_wgp_") as tmpdir:
        tmp = Path(tmpdir)
        queue_path = tmp / "queue.zip"
        wgp_output = tmp / "output"
        wgp_output.mkdir()

        _build_queue_zip(tasks, queue_path)
        _run_wangp(queue_path, wgp_output, bridge_cfg)
        generated = _collect_outputs(wgp_output, task_panel_ids, project)
        results.update(generated)

    return results


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


def _load_bridge_config(config: dict[str, Any]) -> dict[str, Any]:
    """Extract and validate the ``wan2gp`` section with defaults."""
    wgp_root = cfg_get(config, "wan2gp.wgp_root", None)
    python_bin = cfg_get(config, "wan2gp.python_bin", None)

    if not wgp_root or not python_bin:
        raise RuntimeError(
            "wan2gp.wgp_root and wan2gp.python_bin must be set in "
            "lazycomics_config.yaml — they point to your Pinokio "
            "Wan2GP install path and Python env binary."
        )

    return {
        "wgp_root": Path(wgp_root).expanduser().resolve(),
        "python_bin": Path(python_bin).expanduser().resolve(),
        "architecture": cfg_get(config, "wan2gp.architecture", "flux2_klein_9b"),
        "default_steps": cfg_get(config, "wan2gp.default_steps", 4),
        "guidance_scale": cfg_get(config, "wan2gp.guidance_scale", 5),
        "video_prompt_type": cfg_get(config, "wan2gp.video_prompt_type", "KI"),
        "base_resolution": cfg_get(config, "wan2gp.base_resolution", 1024),
        "seed": cfg_get(config, "wan2gp.seed", None),
        "cli_args": cfg_get(config, "wan2gp.cli_args", []),
    }


# ---------------------------------------------------------------------------
# Task construction — THE ADAPTER (sole Wan2GP-specific function)
# ---------------------------------------------------------------------------


def _build_wangp_task(
    panel_data: dict[str, Any],
    prompt: str,
    negative_prompt: str,
    refs: list[Path],
    resolution: str,
    bridge_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Construct one Wan2GP settings dict for a single panel.

    **This is the sole adapter between lazycomics and Wan2GP.** If your
    Wan2GP version uses different field names, update this function only.

    Field names verified against an exported ``flux2_klein_9b`` settings
    JSON from Wan2GP v11.61.
    """
    task: dict[str, Any] = {
        "model_type": bridge_cfg["architecture"],
        "image_mode": 1,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "resolution": resolution,
        "num_inference_steps": bridge_cfg["default_steps"],
        "guidance_scale": bridge_cfg.get("guidance_scale", 5),
        "batch_size": 1,
        "seed": bridge_cfg["seed"] if bridge_cfg["seed"] is not None else -1,
    }

    # Primary ref → image_start (Kontext/Klein latent-stitching input).
    # Supporting refs → image_refs (additional conditioning).
    if refs:
        task["image_start"] = str(refs[0])
    if len(refs) > 1:
        task["image_refs"] = [str(r) for r in refs[1:]]

    # Auto-adjust video_prompt_type based on provided refs if not tightly bound
    if "video_prompt_type" not in bridge_cfg:
        if len(refs) == 0:
            task["video_prompt_type"] = "T"
        elif len(refs) == 1:
            task["video_prompt_type"] = "K"
        else:
            task["video_prompt_type"] = "KI"
    else:
        task["video_prompt_type"] = bridge_cfg["video_prompt_type"]

    return task


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _compute_resolution(aspect_ratio: float, base_resolution: int) -> str:
    """Convert a panel aspect ratio to a ``"WxH"`` resolution string.

    The long edge is ``base_resolution``; the short edge is derived from
    the aspect ratio and snapped to the nearest multiple of 64 (a common
    requirement for diffusion model latent dimensions).
    """
    if aspect_ratio >= 1.0:
        # Landscape or square: width is the long edge.
        w = base_resolution
        h = max(64, _snap_64(int(round(base_resolution / aspect_ratio))))
    else:
        # Portrait: height is the long edge.
        h = base_resolution
        w = max(64, _snap_64(int(round(base_resolution * aspect_ratio))))

    return f"{w}x{h}"


def _snap_64(value: int) -> int:
    """Round to the nearest multiple of 64."""
    return ((value + 32) // 64) * 64


# ---------------------------------------------------------------------------
# Prompt / ref reading
# ---------------------------------------------------------------------------


def _read_prompt(prompts_dir: Path, panel_id: str, *, negative: bool = False) -> str:
    """Read a prompt text file, returning ``""`` if the file is absent."""
    suffix = ".neg.txt" if negative else ".txt"
    path = prompts_dir / f"{panel_id}{suffix}"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _collect_refs(refs_dir: Path, panel_id: str) -> list[Path]:
    """Return ``[primary, *supporting]`` ref images for a panel.

    Matches the naming convention from ``ref_preparer``::

        page_1_panel_1_ref.png       # primary
        page_1_panel_1_ref_1.png     # supporting
        page_1_panel_1_ref_2.png     # supporting
    """
    refs: list[Path] = []
    primary = refs_dir / f"{panel_id}_ref.png"
    if primary.is_file():
        refs.append(primary)

    idx = 1
    while True:
        supporting = refs_dir / f"{panel_id}_ref_{idx}.png"
        if supporting.is_file():
            refs.append(supporting)
            idx += 1
        else:
            break

    return refs


# ---------------------------------------------------------------------------
# Queue building
# ---------------------------------------------------------------------------


def _build_queue_zip(tasks: list[dict[str, Any]], queue_path: Path) -> None:
    """Assemble a Wan2GP queue.zip with settings JSON + embedded images.

    Queue.zip format (from Wan2GP CLI docs): a ZIP containing a
    ``queue.json`` plus any referenced image files. Image paths in
    the settings are rewritten to point to their filename within the ZIP.
    """
    with zipfile.ZipFile(queue_path, "w", zipfile.ZIP_DEFLATED) as zf:
        packaged_tasks = []
        for task_idx, task in enumerate(tasks):
            task_copy = dict(task)

            # Embed image_start
            if "image_start" in task_copy:
                src = Path(task_copy["image_start"])
                if src.is_file():
                    zip_name = f"task{task_idx}_image_start_0{src.suffix}"
                    zf.write(src, zip_name)
                    task_copy["image_start"] = zip_name
                else:
                    del task_copy["image_start"]

            # Embed image_refs
            if "image_refs" in task_copy:
                embedded_refs = []
                for ref_idx, ref_path_str in enumerate(task_copy["image_refs"]):
                    src = Path(ref_path_str)
                    if src.is_file():
                        zip_name = f"task{task_idx}_image_refs_{ref_idx}{src.suffix}"
                        zf.write(src, zip_name)
                        embedded_refs.append(zip_name)
                if embedded_refs:
                    task_copy["image_refs"] = embedded_refs
                else:
                    del task_copy["image_refs"]

            packaged_tasks.append({
                "id": task_idx + 1,
                "params": task_copy
            })

        zf.writestr("queue.json", json.dumps(packaged_tasks, indent=2))


# ---------------------------------------------------------------------------
# Wan2GP execution
# ---------------------------------------------------------------------------


def _run_wangp(
    queue_path: Path,
    output_dir: Path,
    bridge_cfg: dict[str, Any],
) -> None:
    """Shell out to Wan2GP CLI inside the Pinokio-managed env.

    Raises ``RuntimeError`` if the process exits non-zero.
    """
    cmd = [
        str(bridge_cfg["python_bin"]),
        "wgp.py",
        "--process", str(queue_path),
        "--output-dir", str(output_dir),
    ]
    cmd.extend(bridge_cfg["cli_args"])

    print(f"[wan2gp] running: {' '.join(cmd)}")
    print(f"[wan2gp] cwd: {bridge_cfg['wgp_root']}")

    result = subprocess.run(
        cmd,
        cwd=str(bridge_cfg["wgp_root"]),
        capture_output=True,
        text=True,
    )

    if result.stdout:
        for line in result.stdout.strip().splitlines():
            print(f"[wan2gp] {line}")

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "(no stderr)"
        raise RuntimeError(
            f"Wan2GP exited with code {result.returncode}:\n{stderr}"
        )


# ---------------------------------------------------------------------------
# Output collection
# ---------------------------------------------------------------------------


def _collect_outputs(
    wgp_output_dir: Path,
    panel_ids: list[str],
    project: Project,
) -> dict[str, Path]:
    """Move generated images from Wan2GP output dir to ``panels/``.

    Wan2GP writes outputs sequentially (task order matches panel_ids
    order). We glob for image files, sort them, and pair with panel_ids
    positionally. If the count doesn't match, we log and pair what we can.
    """
    output_images = sorted(
        p for p in wgp_output_dir.rglob("*")
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        and p.is_file()
    )

    if len(output_images) != len(panel_ids):
        print(
            f"[wan2gp] WARNING: expected {len(panel_ids)} images, "
            f"found {len(output_images)} in output dir"
        )

    results: dict[str, Path] = {}
    for pid, src in zip(panel_ids, output_images):
        dst = project.panels_dir / f"{pid}.png"
        shutil.copy2(src, dst)
        results[pid] = dst
        print(f"[wan2gp] {pid} -> {dst.name}")

    return results
