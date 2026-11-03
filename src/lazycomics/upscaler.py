"""Panel upscaling — ``panels/`` → ``panels_upscaled/`` via an ONNX model.

Runs between generation and lettering::

    panels/*.png  →  upscale_panels()  →  panels_upscaled/*.png  →  render_text()

Panels are generated at 1024px on the long edge, and assembly used to
stretch them up to ~3x (Lanczos) to fill a 3000px page, lettering included.
Upscaling the raw art first means assembly only ever shrinks it, and
``render_text`` letters the upscaled copy at full size (it still lays out on
the generated panel, so the lettering is the same, just sharper).

The model is an ONNX export of an ESRGAN-family upscaler, run in-process with
``onnxruntime`` (``onnxruntime-directml`` on Windows uses the GPU; no torch
or CUDA). Its scale is read off the model. Chosen in W9 (Docs/PLAN.md):
``realesrgan-x4plus-anime``, ~2s a panel on an RTX 3080 Laptop. Install
steps are in the README, "Upscaling".

Each upscaled PNG records the SHA-1 of the panel it was made from
(``SOURCE_KEY``), the same hash ``render_text`` keys detected faces on. A
regenerated panel is re-upscaled on the next run; ``render_text`` refuses an
upscaled copy of a different image.
"""

from __future__ import annotations

import hashlib
import math
import time
from pathlib import Path
from typing import Any

from PIL import Image, PngImagePlugin

from lazycomics.config import cfg_get, load_config
from lazycomics.models import Project

__all__ = ["upscale_panels", "SOURCE_KEY"]


SOURCE_KEY = "lazycomics_source_sha1"

# Tiles keep every model call one fixed shape (DirectML compiles per shape)
# and bound VRAM. Each is read with _TILE_PAD_PX of context on every side,
# then cropped, so tile edges don't show.
_TILE_PX = 384
_TILE_PAD_PX = 24
_GPU_PROVIDERS = ("DmlExecutionProvider", "CUDAExecutionProvider")


def upscale_panels(
    project: Project,
    panels: list[str] | None = None,
    *,
    force: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Upscale each generated panel with the configured ONNX model.

    Reads ``<project>/panels/<panel_id>.png``, writes
    ``<project>/panels_upscaled/<panel_id>.png``. Returns
    ``{panel_id: output_path}``.

    A panel whose upscaled copy was made from the current ``panels/`` image
    is skipped unless ``force=True``; one made from an older image is redone.
    ``upscaler.model_path`` must point at the ONNX model.
    """
    if config is None:
        config = load_config()
    model_path = cfg_get(config, "upscaler.model_path", None)
    if not model_path or not Path(model_path).is_file():
        raise FileNotFoundError(
            f"upscaler.model_path is {model_path!r}: point it at the ONNX upscaler "
            "(README, \"Upscaling\")"
        )

    if panels is None:
        panel_ids = sorted(p.stem for p in project.panels_dir.glob("*.png"))
    else:
        panel_ids = list(panels)

    results: dict[str, Path] = {}
    todo: list[tuple[str, Path, str]] = []
    for panel_id in panel_ids:
        src = project.panels_dir / f"{panel_id}.png"
        if not src.is_file():
            print(f"[upscaler] skipping {panel_id}: no source panel at {src}")
            continue
        out = project.panels_upscaled_dir / f"{panel_id}.png"
        source_hash = hashlib.sha1(src.read_bytes()).hexdigest()
        if out.is_file() and not force and _made_from(out) == source_hash:
            results[panel_id] = out
            continue
        todo.append((panel_id, src, source_hash))
    if not todo:
        return results

    session = _open_session(Path(model_path))
    project.panels_upscaled_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    for panel_id, src, source_hash in todo:
        with Image.open(src) as im:
            up = _upscale_image(session, im.convert("RGB"))
        info = PngImagePlugin.PngInfo()
        info.add_text(SOURCE_KEY, source_hash)
        out = project.panels_upscaled_dir / f"{panel_id}.png"
        up.save(out, pnginfo=info)
        results[panel_id] = out
    took = time.perf_counter() - started
    print(f"[upscaler] {len(todo)} panels upscaled with {Path(model_path).name} "
          f"in {took:.1f}s ({took / len(todo):.1f}s each)")
    return results


def _made_from(path: Path) -> str | None:
    """The source hash an upscaled PNG records, or ``None`` (e.g. hand-edited)."""
    with Image.open(path) as im:
        return im.info.get(SOURCE_KEY)


# ---------------------------------------------------------------------------
# Model (module-level so tests can monkeypatch)
# ---------------------------------------------------------------------------


def _open_session(model_path: Path):
    """An ``onnxruntime`` session on the GPU if a GPU provider is installed."""
    import onnxruntime as ort  # lazy: optional dependency (lazycomics[upscale])

    available = ort.get_available_providers()
    gpu = [p for p in _GPU_PROVIDERS if p in available]
    if not gpu:
        print("[upscaler] WARNING: no GPU provider in onnxruntime "
              f"({available}); running on the CPU, expect minutes per panel. "
              "On Windows install onnxruntime-directml.")
    options = ort.SessionOptions()
    if "DmlExecutionProvider" in gpu:
        # DirectML supports neither memory patterns nor parallel execution.
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(str(model_path), options,
                                providers=gpu + ["CPUExecutionProvider"])


def _upscale_image(session, img: Image.Image) -> Image.Image:
    """Run ``session`` over ``img`` tile by tile; the output is ``scale`` x the input."""
    import numpy as np

    inp = session.get_inputs()[0]
    dtype = np.float16 if "float16" in inp.type else np.float32
    arr = np.asarray(img, dtype=np.float32) / 255.0
    h, w = arr.shape[:2]
    core, pad = _TILE_PX, _TILE_PAD_PX
    nh, nw = math.ceil(h / core) * core, math.ceil(w / core) * core
    # Reflect into the padding; "edge" where the panel is too small to reflect.
    pads = ((pad, pad + nh - h), (pad, pad + nw - w), (0, 0))
    mode = "reflect" if max(pads[0]) < h and max(pads[1]) < w else "edge"
    padded = np.pad(arr, pads, mode=mode)

    out = None
    for y in range(0, nh, core):
        for x in range(0, nw, core):
            tile = padded[y:y + core + 2 * pad, x:x + core + 2 * pad]
            tile = np.ascontiguousarray(tile.transpose(2, 0, 1)[None], dtype=dtype)
            res = session.run(None, {inp.name: tile})[0][0].transpose(1, 2, 0)
            if out is None:
                scale = res.shape[0] // (core + 2 * pad)
                out = np.zeros((nh * scale, nw * scale, 3), np.float32)
            p = pad * scale
            out[y * scale:(y + core) * scale, x * scale:(x + core) * scale] = \
                res[p:p + core * scale, p:p + core * scale]
    out = np.clip(out[:h * scale, :w * scale], 0.0, 1.0)
    return Image.fromarray((out * 255.0 + 0.5).astype(np.uint8))
