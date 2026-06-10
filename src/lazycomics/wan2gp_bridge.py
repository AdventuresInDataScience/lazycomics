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
      python_bin: C:\\pinokio\\api\\wan.git\\app\\env\\Scripts\\python.exe
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
import time
import zipfile
from pathlib import Path
from typing import Any

from lazycomics.config import cfg_get, load_config
from lazycomics.geometry import resolution_for_aspect
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

    # Ensure panels/ exists
    project.panels_dir.mkdir(parents=True, exist_ok=True)

    # Partition into single-pass (batchable) and multi-inpaint (sequential)
    single_tasks: list[dict[str, Any]] = []
    single_ids: list[str] = []
    multi_panels: list[tuple[str, dict[str, Any]]] = []

    for pid in to_generate:
        enriched_path = project.enriched_dir / f"{pid}.json"
        if not enriched_path.is_file():
            print(f"[wan2gp] skipping {pid}: no enriched JSON")
            continue

        panel = json.loads(enriched_path.read_text(encoding="utf-8"))
        strategy = panel.get("char_generation_strategy", "single")

        if strategy == "multi_inpaint":
            multi_panels.append((pid, panel))
        else:
            task = _build_panel_task(panel, pid, project, bridge_cfg)
            single_tasks.append(task)
            single_ids.append(pid)

    total = len(single_ids) + len(multi_panels)
    print(
        f"[wan2gp] generating {total} panel(s): "
        f"{len(single_ids)} single + {len(multi_panels)} multi-inpaint",
        flush=True,
    )

    # --- Batch: all single-strategy panels in one queue ---
    if single_tasks:
        print(
            f"[wan2gp] > batch of {len(single_tasks)} panel(s): "
            f"{', '.join(single_ids)}",
            flush=True,
        )
        with tempfile.TemporaryDirectory(prefix="lazycomics_wgp_") as tmpdir:
            tmp = Path(tmpdir)
            queue_path = tmp / "queue.zip"
            wgp_output = tmp / "output"
            wgp_output.mkdir()
            _build_queue_zip(single_tasks, queue_path)
            _run_wangp(queue_path, wgp_output, bridge_cfg)
            results.update(_collect_outputs(wgp_output, single_ids, project))

    # --- Sequential: each multi-inpaint panel runs base + N inpaint passes ---
    for pid, panel in multi_panels:
        chars = panel.get("inpaint_order") or []
        print(
            f"[wan2gp] > multi-inpaint {pid} "
            f"({len(chars)} passes for {' -> '.join(chars)})",
            flush=True,
        )
        result_path = _run_multi_inpaint(pid, panel, project, bridge_cfg)
        if result_path:
            results[pid] = result_path

    return results


def _build_panel_task(
    panel: dict[str, Any],
    pid: str,
    project: Project,
    bridge_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Build a single image-gen task for a panel (shared by single + base pass)."""
    prompt = _read_prompt(project.prompts_dir, pid)
    neg_prompt = _read_prompt(project.prompts_dir, pid, negative=True)
    refs = _collect_refs(project.refs_prepared_dir, pid)
    resolution = _compute_resolution(
        float(panel.get("aspect_ratio") or 1.0),
        bridge_cfg["base_resolution"],
    )

    lora_names, lora_mults, trigger_words = _collect_loras(panel, bridge_cfg)

    full_prompt = prompt
    if trigger_words:
        prefix = ", ".join(trigger_words)
        full_prompt = f"{prefix}, {prompt}" if prompt else prefix

    return _build_wangp_task(
        panel_data=panel,
        prompt=full_prompt,
        negative_prompt=neg_prompt,
        refs=refs,
        resolution=resolution,
        bridge_cfg=bridge_cfg,
        activated_loras=lora_names,
        loras_multipliers=lora_mults,
    )


# ---------------------------------------------------------------------------
# Multi-inpaint orchestration
# ---------------------------------------------------------------------------


def _run_multi_inpaint(
    pid: str,
    panel: dict[str, Any],
    project: Project,
    bridge_cfg: dict[str, Any],
) -> Path | None:
    """Generate a multi-character panel: base pass + sequential inpaints.

    1. Base pass — image_mode 1, full prompt, primary character's LoRA + ref.
    2. For each character in ``inpaint_order[1:]``:
       - Create a mask image (white = region for the new character).
       - Inpaint task (image_mode 2) with current panel + mask + character prompt.
       - Result becomes input for the next pass.

    Returns the final panel path, or ``None`` on failure.
    """
    inpaint_order = panel.get("inpaint_order") or []
    if len(inpaint_order) < 2:
        # Shouldn't be routed here, but handle gracefully
        task = _build_panel_task(panel, pid, project, bridge_cfg)
        return _run_single_task_and_collect(task, pid, project, bridge_cfg)

    resolution = _compute_resolution(
        float(panel.get("aspect_ratio") or 1.0),
        bridge_cfg["base_resolution"],
    )
    w, h = (int(x) for x in resolution.split("x"))

    total_passes = len(inpaint_order)

    # --- Base pass: primary character ---
    primary = inpaint_order[0]
    print(f"[wan2gp]   pass 1/{total_passes} (base): {primary}", flush=True)
    base_task = _build_panel_task(panel, pid, project, bridge_cfg)
    current_panel = _run_single_task_and_collect(
        base_task, pid, project, bridge_cfg,
    )
    if not current_panel:
        return None

    # --- Inpaint passes: remaining characters ---
    chars_lookup = {
        c["identifier"]: c for c in (panel.get("characters") or [])
    }
    bubble_lookup = {
        b["character"]: b for b in (panel.get("bubble_layout") or [])
    }

    for pass_idx, char_name in enumerate(inpaint_order[1:], start=1):
        print(
            f"[wan2gp]   pass {pass_idx + 1}/{total_passes} (inpaint): {char_name}",
            flush=True,
        )

        char_data = chars_lookup.get(char_name, {})

        # Build mask from bubble_layout position (or default zone)
        mask_region = _get_character_region(
            char_name, bubble_lookup, len(inpaint_order), pass_idx,
        )
        mask_path = _generate_mask(
            w, h, mask_region, project, pid, pass_idx,
            feather=bridge_cfg["inpaint_mask_feather"],
        )

        # Build character-focused prompt
        char_prompt = _build_inpaint_prompt(char_data, panel)
        neg_prompt = _read_prompt(project.prompts_dir, pid, negative=True)

        # Collect LoRA for just this character
        char_loras, char_mults, char_triggers = _collect_loras(
            {"characters": [char_data]}, bridge_cfg,
        )
        if char_triggers:
            char_prompt = f"{', '.join(char_triggers)}, {char_prompt}"

        inpaint_task = _build_inpaint_task(
            source_image=current_panel,
            mask_image=mask_path,
            prompt=char_prompt,
            negative_prompt=neg_prompt,
            resolution=resolution,
            bridge_cfg=bridge_cfg,
            activated_loras=char_loras,
            loras_multipliers=char_mults,
        )

        result = _run_single_task_and_collect(
            inpaint_task, pid, project, bridge_cfg,
        )
        if result:
            current_panel = result
        else:
            print(f"[wan2gp]   WARNING: inpaint pass {pass_idx} failed for {char_name}")
            break

    return current_panel


def _run_single_task_and_collect(
    task: dict[str, Any],
    pid: str,
    project: Project,
    bridge_cfg: dict[str, Any],
) -> Path | None:
    """Run one task through Wan2GP and copy output to panels/."""
    with tempfile.TemporaryDirectory(prefix="lazycomics_wgp_") as tmpdir:
        tmp = Path(tmpdir)
        queue_path = tmp / "queue.zip"
        wgp_output = tmp / "output"
        wgp_output.mkdir()
        _build_queue_zip([task], queue_path)
        try:
            _run_wangp(queue_path, wgp_output, bridge_cfg)
        except RuntimeError as e:
            print(f"[wan2gp]   ERROR: {e}")
            return None
        collected = _collect_outputs(wgp_output, [pid], project)
        return collected.get(pid)


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


# Inpaint defaults (multi-character panels). Exposed as module constants so
# the shipped config and the config-parity test stay in lockstep.
#   denoising  — how much of the masked area is regenerated. 0.15-0.35 keeps
#                the base panel's structure (the new character is painted *into*
#                the existing scene rather than the half being redrawn from
#                scratch, which is what produced the "two disjointed halves").
#                0.25 is the middle of that range.
#   feather    — Gaussian-blur radius applied to the mask, as a fraction of the
#                panel's short edge. Soft mask edges blend the inpaint into the
#                surrounding art instead of leaving a hard seam.
#   mask_expand— Wan2GP-side dilation of the mask (pixels). Left at 0 since the
#                lazycomics-generated mask already grows + feathers its own edge.
_DEFAULT_INPAINT_DENOISING = 0.25
_DEFAULT_INPAINT_MASKING_STRENGTH = 0.3
_DEFAULT_INPAINT_MASK_EXPAND = 0
_DEFAULT_INPAINT_MASK_FEATHER = 0.04


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
        "guidance_scale": cfg_get(config, "wan2gp.guidance_scale", 3.5),
        "video_prompt_type": cfg_get(config, "wan2gp.video_prompt_type", "KI"),
        "base_resolution": cfg_get(config, "wan2gp.base_resolution", 1024),
        "seed": cfg_get(config, "wan2gp.seed", 42),
        "cli_args": cfg_get(config, "wan2gp.cli_args", []),
        "loras_dir": Path(
            cfg_get(config, "wan2gp.loras_dir", "")
            or str(Path(wgp_root).expanduser().resolve() / "loras")
        ).expanduser().resolve(),
        "inpaint_denoising": cfg_get(config, "wan2gp.inpaint_denoising", _DEFAULT_INPAINT_DENOISING),
        "inpaint_masking_strength": cfg_get(config, "wan2gp.inpaint_masking_strength", _DEFAULT_INPAINT_MASKING_STRENGTH),
        "inpaint_mask_expand": cfg_get(config, "wan2gp.inpaint_mask_expand", _DEFAULT_INPAINT_MASK_EXPAND),
        "inpaint_mask_feather": cfg_get(config, "wan2gp.inpaint_mask_feather", _DEFAULT_INPAINT_MASK_FEATHER),
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
    activated_loras: list[str] | None = None,
    loras_multipliers: str = "",
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
        "activated_loras": activated_loras or [],
        "loras_multipliers": loras_multipliers,
    }

    # Primary ref → image_start (Kontext/Klein latent-stitching input).
    # Supporting refs → image_refs (additional conditioning).
    if refs:
        task["image_start"] = str(refs[0])
        _warn_on_resolution_mismatch(refs[0], resolution, panel_data.get("panel_id", "?"))
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


def _build_inpaint_task(
    source_image: Path,
    mask_image: Path,
    prompt: str,
    negative_prompt: str,
    resolution: str,
    bridge_cfg: dict[str, Any],
    activated_loras: list[str] | None = None,
    loras_multipliers: str = "",
) -> dict[str, Any]:
    """Construct a Wan2GP inpaint settings dict.

    Differences from image gen: ``image_mode: 2``,
    ``video_prompt_type: "VAG"``, source in ``image_guide``,
    mask in ``image_mask``, plus ``denoising_strength`` and
    ``masking_strength``.

    The field names follow Wan2GP's ``ATTACHMENT_KEYS`` (wgp.py:142).
    Using ``image_start``/``image_end`` here causes Wan2GP to reject the
    task with "You must provide a Control Image" because ``V`` in
    ``video_prompt_type`` requires ``image_guide``.
    """
    return {
        "model_type": bridge_cfg["architecture"],
        "image_mode": 2,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "resolution": resolution,
        "num_inference_steps": bridge_cfg["default_steps"],
        "guidance_scale": bridge_cfg.get("guidance_scale", 3.5),
        "batch_size": 1,
        "seed": bridge_cfg["seed"] if bridge_cfg["seed"] is not None else -1,
        "video_prompt_type": "VAG",
        "denoising_strength": bridge_cfg.get("inpaint_denoising", _DEFAULT_INPAINT_DENOISING),
        "masking_strength": bridge_cfg.get("inpaint_masking_strength", _DEFAULT_INPAINT_MASKING_STRENGTH),
        "mask_expand": bridge_cfg.get("inpaint_mask_expand", _DEFAULT_INPAINT_MASK_EXPAND),
        "image_guide": str(source_image),
        "image_mask": str(mask_image),
        "activated_loras": activated_loras or [],
        "loras_multipliers": loras_multipliers,
    }


# ---------------------------------------------------------------------------
# Mask generation
# ---------------------------------------------------------------------------


def _get_character_region(
    char_name: str,
    bubble_lookup: dict[str, dict[str, Any]],
    total_chars: int,
    char_index: int,
) -> tuple[float, float, float, float]:
    """Return ``(x_frac, y_frac, w_frac, h_frac)`` for a character's region.

    Uses ``bubble_layout`` position if available (character is near their
    speech bubble). Falls back to even horizontal distribution.

    The region is inset slightly from the top and bottom edges so the mask
    reads as a standing-figure band rather than a full-panel-height slab.
    A slab that spans the entire height and half the width is what makes a
    multi-character panel come out as two disjointed halves; combined with
    the feathered mask edge in :func:`_generate_mask`, the inset keeps the
    repaint confined to where the character actually stands.
    """
    # Vertical band: small top margin (usually background/sky), reach the
    # bottom edge (feet). Tunable here rather than per-call.
    y = 0.06
    h = 0.94

    bubble = bubble_lookup.get(char_name)
    if bubble:
        # Centre the mask on the bubble's x position.
        cx = bubble["x_frac"] + bubble["width_frac"] / 2
        region_w = max(0.25, 1.0 / total_chars)
        x = max(0.0, cx - region_w / 2)
        return (x, y, min(region_w, 1.0 - x), h)

    # Fallback: even horizontal stripe
    stripe_w = 1.0 / total_chars
    x = char_index * stripe_w
    return (x, y, stripe_w, h)


def _generate_mask(
    width: int,
    height: int,
    region: tuple[float, float, float, float],
    project: Project,
    pid: str,
    pass_idx: int,
    *,
    feather: float = _DEFAULT_INPAINT_MASK_FEATHER,
) -> Path:
    """Create a soft inpaint mask PNG (black = preserve, white = repaint).

    Unlike a hard 0/255 rectangle — which leaves a visible seam where the
    repainted region butts against the untouched art — this mask:

    * draws a **rounded** rectangle (corners softened, no sharp box);
    * **grows** the box outward by roughly the feather radius first, so the
      blur in the next step eats into the margin rather than the character's
      core region;
    * **feathers** the whole mask with a Gaussian blur of radius
      ``feather * min(width, height)``, producing a gradient edge that lets
      the inpaint blend smoothly into the surrounding panel.

    ``feather`` is a fraction of the panel's short edge (``0`` disables the
    blur and yields a plain rounded rect). Saved under ``panels/_masks/`` so
    it persists long enough for the queue zip to embed it.
    """
    from PIL import Image, ImageDraw, ImageFilter

    x_frac, y_frac, w_frac, h_frac = region
    x0 = int(x_frac * width)
    y0 = int(y_frac * height)
    x1 = int((x_frac + w_frac) * width)
    y1 = int((y_frac + h_frac) * height)

    feather_px = max(0, int(feather * min(width, height)))

    # Grow the box by the feather radius (clamped to the canvas) so the blur
    # softens the edge without shrinking the intended repaint core.
    gx0 = max(0, x0 - feather_px)
    gy0 = max(0, y0 - feather_px)
    gx1 = min(width, x1 + feather_px)
    gy1 = min(height, y1 + feather_px)

    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    radius = int(min(gx1 - gx0, gy1 - gy0) * 0.35)
    if radius > 0:
        draw.rounded_rectangle([gx0, gy0, gx1, gy1], radius=radius, fill=255)
    else:
        draw.rectangle([gx0, gy0, gx1, gy1], fill=255)

    if feather_px > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(feather_px))

    masks_dir = project.panels_dir / "_masks"
    masks_dir.mkdir(parents=True, exist_ok=True)
    mask_path = masks_dir / f"{pid}_mask_{pass_idx}.png"
    mask.save(mask_path)
    return mask_path


def _build_inpaint_prompt(
    char_data: dict[str, Any],
    panel: dict[str, Any],
) -> str:
    """Build a character-focused prompt for an inpaint pass.

    Combines the character's visual description with the panel's mood
    and shot context. Falls back to the character identifier if no
    description is available.
    """
    parts: list[str] = []

    desc = char_data.get("visual_description") or char_data.get("identifier", "character")
    parts.append(desc)

    mood = panel.get("mood")
    if mood:
        parts.append(f"{mood} mood")

    action = panel.get("action")
    if action:
        parts.append(action)

    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _warn_on_resolution_mismatch(
    image_start: Path, resolution: str, panel_id: str, tol: float = 0.05,
) -> None:
    """Warn when ``image_start``'s pixel size diverges from ``resolution``.

    Klein takes its output aspect from ``image_start``, so if the prepared
    primary ref isn't (close to) the requested ``WxH`` the panel comes out
    the wrong shape. ``ref_preparer`` now sizes the primary exactly, so this
    should never fire — it's a tripwire that catches a future regression
    (e.g. ``working_resolution`` drifting from ``base_resolution``).
    """
    try:
        want_w, want_h = (int(x) for x in resolution.split("x"))
        from PIL import Image
        with Image.open(image_start) as im:
            got_w, got_h = im.size
    except Exception:
        return  # never let a diagnostic break generation
    if want_w <= 0 or want_h <= 0:
        return
    dw = abs(got_w - want_w) / want_w
    dh = abs(got_h - want_h) / want_h
    if dw > tol or dh > tol:
        print(
            f"[wan2gp] WARNING: {panel_id} image_start is {got_w}x{got_h} but "
            f"requested {want_w}x{want_h} — Klein keys output aspect off "
            f"image_start, so the panel may come out the wrong shape.",
            flush=True,
        )


def _compute_resolution(aspect_ratio: float, base_resolution: int) -> str:
    """Convert a panel aspect ratio to a ``"WxH"`` resolution string.

    Delegates to :func:`lazycomics.geometry.resolution_for_aspect` so the
    bridge and ``ref_preparer`` derive panel dimensions from one place — the
    prepared ``image_start`` reference must match this exactly or Klein
    snaps it to the wrong shape (see ``geometry`` module docstring).
    """
    w, h = resolution_for_aspect(aspect_ratio, base_resolution)
    return f"{w}x{h}"


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


def _collect_loras(
    panel_data: dict[str, Any],
    bridge_cfg: dict[str, Any],
) -> tuple[list[str], str, list[str]]:
    """Extract LoRA filenames + weights from panel characters.

    Returns ``(activated_loras, loras_multipliers, trigger_words)`` where:
    - ``activated_loras`` is a list of ``.safetensors`` filenames
    - ``loras_multipliers`` is a comma-separated weight string (``""`` if
      all defaults)
    - ``trigger_words`` is a list of trigger words to prepend to the prompt

    LoRA files registered at arbitrary paths are copied into Wan2GP's
    ``loras/`` directory so the headless CLI can find them by filename.
    """
    filenames: list[str] = []
    weights: list[str] = []
    triggers: list[str] = []
    loras_dir = bridge_cfg["loras_dir"]

    for char in panel_data.get("characters") or []:
        lora = char.get("lora")
        if not lora or not lora.get("path"):
            continue

        src = Path(lora["path"])
        if not src.is_file():
            print(f"[wan2gp] WARNING: LoRA not found: {src}")
            continue

        # Ensure LoRA is available in Wan2GP's loras dir
        dst = _ensure_lora(src, loras_dir)
        filenames.append(dst.name)
        weights.append(str(lora.get("weight", 0.7)))

        tw = lora.get("trigger_word")
        if tw:
            triggers.append(tw)

    multipliers = ",".join(weights) if weights else ""
    return filenames, multipliers, triggers


def _ensure_lora(src: Path, loras_dir: Path) -> Path:
    """Copy a LoRA file into Wan2GP's loras dir if not already there.

    Returns the destination path (always inside ``loras_dir``).
    """
    dst = loras_dir / src.name
    if dst.is_file() and dst.stat().st_size == src.stat().st_size:
        return dst  # already present, skip copy
    loras_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"[wan2gp] copied LoRA: {src.name} -> {loras_dir}")
    return dst


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

            # Embed single-image attachment keys. image_start/end are the
            # base-pass inputs (Kontext); image_guide/mask are the inpaint
            # inputs (control image + mask). See ATTACHMENT_KEYS in wgp.py.
            for key in ("image_start", "image_end", "image_guide", "image_mask"):
                if key in task_copy:
                    src = Path(task_copy[key])
                    if src.is_file():
                        zip_name = f"task{task_idx}_{key}_0{src.suffix}"
                        zf.write(src, zip_name)
                        task_copy[key] = zip_name
                    else:
                        del task_copy[key]

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

    Inherits stdout/stderr so Wan2GP's own progress output (step counts,
    tqdm bars) streams to the caller's terminal in real time. Without
    this, capture_output buffers the whole run and the terminal looks
    frozen for the duration of generation.

    Raises ``RuntimeError`` if the process exits non-zero.
    """
    cmd = [
        str(bridge_cfg["python_bin"]),
        "wgp.py",
        "--process", str(queue_path),
        "--output-dir", str(output_dir),
    ]
    cmd.extend(bridge_cfg["cli_args"])

    print(f"[wan2gp]   running: {' '.join(cmd)}", flush=True)
    start = time.perf_counter()

    # stdout/stderr inherited (no capture_output) — output streams live.
    result = subprocess.run(cmd, cwd=str(bridge_cfg["wgp_root"]))

    elapsed = time.perf_counter() - start
    if result.returncode != 0:
        raise RuntimeError(
            f"Wan2GP exited with code {result.returncode} "
            f"(after {elapsed:.1f}s). See output above for details."
        )
    print(f"[wan2gp]   done in {elapsed:.1f}s", flush=True)


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

    Wan2GP extracts queue contents into ``output/_loaded_queue_cache/``
    (wgp.py:1535) — we must skip that directory, otherwise the input
    images get paired as if they were generated outputs.
    """
    output_images = sorted(
        p for p in wgp_output_dir.rglob("*")
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        and p.is_file()
        and "_loaded_queue_cache" not in p.parts
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
