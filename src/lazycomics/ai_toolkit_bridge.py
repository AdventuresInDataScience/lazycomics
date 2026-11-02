"""AI Toolkit bridge (W6 — per-character LoRA training).

Trains a character LoRA with AI Toolkit headlessly: writes a job config
and runs ``run.py <config>`` inside the Pinokio-managed env, as
``wan2gp_bridge`` runs ``wgp.py``. No UI, no server.

Per character, outside the per-panel pipeline::

    assets/characters/<id>/references/*
        ↓ generate_training_set()   (Wan2GP Klein: varied shots of the character)
    assets/characters/<id>/lora_dataset/<id>_NN.png + <id>_NN.txt captions
        ↓ hand-pick: move rejects into lora_dataset/rejected/
        ↓ train_character_lora()    (AI Toolkit)
    assets/characters/<id>/lora/<job>/<job>.safetensors
        ↓ asset_registry.set_lora(trigger_word=<id>)
    enrich → build_prompts (trigger first) → generate_panels (activated_loras)

Each character has one reference image, and a LoRA trains on 10-30 varied
ones, so ``generate_training_set`` has Klein draw the character from that
reference in ``_TRAINING_SHOTS``' shots, angles and expressions. Captions
name the trigger plus what varies (shot, angle, expression, background) and
leave out the description, so the look binds to the trigger.

Klein specifics (Docs/PLAN.md, Reference, "AI Toolkit with Klein"):

* AI Toolkit trains Klein on the undistilled base model
  (``black-forest-labs/FLUX.2-klein-base-9B``); Wan2GP runs the distilled
  Klein 9B. Both share the architecture, so the LoRA applies to either.
* That base model is gated on Hugging Face: accept its licence there, then
  give AI Toolkit a token (``HF_TOKEN`` in the environment or in
  ``<root>/.env``, or ``hf auth login``). The first run downloads it
  (~18 GB) plus Qwen3-8B (~16 GB).
* The saved LoRA's keys are ``diffusion_model.{double,single}_blocks.N.
  <module>.lora_A/B.weight``, which Wan2GP's Flux loader takes as-is.

Configuration (``ai_toolkit:`` in ``lazycomics_config.yaml``)::

    ai_toolkit:
      root: C:\\pinokio\\api\\ai-toolkit.git\\app
      python_bin: C:\\pinokio\\api\\ai-toolkit.git\\app\\env\\Scripts\\python.exe
      steps: 2000
      ...

``root`` and ``python_bin`` have no defaults; everything else does.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from lazycomics import wan2gp_bridge
from lazycomics.asset_registry import get_character, get_style, set_lora
from lazycomics.config import cfg_get, load_config
from lazycomics.models import Project
from lazycomics.prompt_builder import DEFAULT_NEGATIVE_PROMPT

__all__ = ["generate_training_set", "train_character_lora"]


# Training defaults. Exposed as module constants so the shipped config and
# the config-parity test stay in lockstep. Model settings follow AI Toolkit's
# own FLUX.2-klein-base-9B preset (ui/src/app/jobs/new/options.ts): 8-bit
# transformer and text encoder, low_vram, "weighted" timesteps.
_DEFAULT_ARCH = "flux2_klein_9b"
_DEFAULT_NAME_OR_PATH = "black-forest-labs/FLUX.2-klein-base-9B"
_DEFAULT_STEPS = 2000
_DEFAULT_RANK = 16
_DEFAULT_LEARNING_RATE = 1e-4
_DEFAULT_RESOLUTIONS = [512, 768, 1024]
_DEFAULT_SAVE_EVERY = 250
_DEFAULT_QUANTIZE = True
_DEFAULT_LOW_VRAM = True
_DEFAULT_LAYER_OFFLOADING = False
_DEFAULT_LORA_WEIGHT = 1.0

# Fewer than this and the run is still allowed, with a warning.
_FEW_TRAINING_IMAGES = 10

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

# (shot, angle, expression, background, aspect ratio) per generated image.
# Covers what a comic asks of a character: close-ups to full figures, front
# to back, a range of expressions, plain and busy backgrounds.
_TRAINING_SHOTS: list[tuple[str, str, str, str, float]] = [
    ("close-up portrait", "front view", "neutral expression", "plain light grey background", 1.0),
    ("close-up portrait", "three-quarter view facing left", "smiling", "plain light grey background", 1.0),
    ("close-up portrait", "three-quarter view facing right", "worried expression", "plain white background", 1.0),
    ("close-up portrait", "side profile facing left", "thoughtful expression", "plain white background", 1.0),
    ("close-up portrait", "side profile facing right", "surprised expression", "plain light grey background", 1.0),
    ("extreme close-up of the face", "front view", "shocked, eyes wide", "plain dark background", 1.0),
    ("close-up portrait", "looking down", "tired expression", "dim bar interior background", 1.0),
    ("close-up portrait", "looking up", "hopeful expression", "city street at night background", 1.0),
    ("medium shot from the waist up", "front view", "arms crossed, annoyed expression", "plain light grey background", 0.75),
    ("medium shot from the waist up", "three-quarter view facing left", "laughing", "office interior background", 0.75),
    ("medium shot from the waist up", "three-quarter view facing right", "talking, gesturing with one hand", "office interior background", 0.75),
    ("medium shot from the waist up", "front view", "holding a coffee mug, sleepy expression", "apartment interior background", 0.75),
    ("medium shot from the waist up", "back view, looking over the shoulder", "suspicious expression", "plain white background", 0.75),
    ("medium shot from the waist up", "side profile facing right", "angry expression", "plain light grey background", 0.75),
    ("full-body shot", "front view", "standing, neutral expression", "plain white background", 2 / 3),
    ("full-body shot", "three-quarter view facing left", "walking, determined expression", "city street background", 2 / 3),
    ("full-body shot", "front view", "sitting on a chair, relaxed expression", "plain light grey background", 2 / 3),
    ("full-body shot", "back view", "standing", "plain white background", 2 / 3),
    ("full-body shot", "side view facing right", "running, panicked expression", "plain light grey background", 2 / 3),
    ("full-body shot", "three-quarter view facing right", "hands in pockets, embarrassed expression", "plain white background", 2 / 3),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_training_set(
    project: Project,
    character_id: str,
    *,
    force: bool = False,
    config: dict[str, Any] | None = None,
) -> list[Path]:
    """Build a character's LoRA training set in ``assets/characters/<id>/lora_dataset/``.

    The character's reference images are copied in (captioned with the
    trigger alone), then Wan2GP draws the character from the first
    reference once per ``_TRAINING_SHOTS`` entry, as ``<id>_NN.png`` with
    a ``<id>_NN.txt`` caption. Each image gets its own seed
    (``wan2gp.seed`` + N) for variety.

    Hand-pick afterwards: move rejects into ``lora_dataset/rejected/``.
    Images already in the dataset or in ``rejected/`` are not generated
    again; ``force=True`` deletes the whole dataset (``rejected/`` too)
    and starts over.

    Returns the dataset's images (``rejected/`` excluded). Raises
    :class:`FileNotFoundError` if the character isn't registered and
    :class:`ValueError` if it has no reference image: without one, Klein
    draws a different person every time.
    """
    if config is None:
        config = load_config()
    char = get_character(project, character_id)
    if not char.reference_images:
        raise ValueError(
            f"{character_id} has no reference image; a LoRA training set is "
            f"drawn from one (register_character(..., reference_images=[...]))."
        )

    dataset = _dataset_dir(project, character_id)
    rejected = dataset / "rejected"
    if force and dataset.exists():
        shutil.rmtree(dataset)
    dataset.mkdir(parents=True, exist_ok=True)

    def kept_or_rejected(name: str) -> bool:
        return any((d / f"{name}{s}").is_file()
                   for d in (dataset, rejected) for s in _IMAGE_SUFFIXES)

    for i, ref in enumerate(char.reference_images, start=1):
        name = f"{character_id}_ref_{i}"
        if not kept_or_rejected(name):
            shutil.copy2(ref, dataset / f"{name}{ref.suffix.lower()}")
            (dataset / f"{name}.txt").write_text(character_id + "\n", encoding="utf-8")

    style = _style_prefix(project)
    seed = cfg_get(config, "wan2gp.seed", 42) or 0
    todo: list[dict[str, Any]] = []
    captions: dict[str, str] = {}
    for n, (shot, angle, expression, background, aspect) in enumerate(_TRAINING_SHOTS, start=1):
        name = f"{character_id}_{n:02d}"
        if kept_or_rejected(name):
            continue
        prompt = (f"{shot[0].upper()}{shot[1:]} of {_display_name(character_id)}, "
                  f"{char.visual_description}, {angle}, {expression}. {background}.")
        if style:
            prompt += f" {style[0].upper()}{style[1:]}"
        todo.append({
            "name": name,
            "prompt": prompt,
            "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
            "refs": [char.reference_images[0]],
            "aspect_ratio": aspect,
            "seed": seed + n,
        })
        captions[name] = ", ".join([character_id, shot, angle, expression, background])

    if todo:
        print(f"[ai_toolkit] drawing {len(todo)} training image(s) of {character_id}", flush=True)
        made = wan2gp_bridge.generate_images(todo, dataset, config=config)
        for name in made:
            (dataset / f"{name}.txt").write_text(captions[name] + "\n", encoding="utf-8")

    return _dataset_images(dataset)


def train_character_lora(
    project: Project,
    character_id: str,
    *,
    force: bool = False,
    config: dict[str, Any] | None = None,
) -> Path:
    """Train a LoRA for a registered character and attach it with ``set_lora``.

    Trains on ``assets/characters/<id>/lora_dataset/`` (see
    :func:`generate_training_set`; ``rejected/`` is ignored) and writes the
    AI Toolkit job under ``assets/characters/<id>/lora/``. The trigger word
    is the character ID; the weight is ``ai_toolkit.lora_weight``.

    Skips training when the LoRA already exists, but still attaches it, so
    re-registering a character and calling this again restores its LoRA.
    ``force=True`` deletes the old job folder and trains from scratch.
    Without it, AI Toolkit resumes an interrupted job from its last
    checkpoint.

    Returns the ``.safetensors`` path. Raises :class:`FileNotFoundError` if
    the character isn't registered or the dataset is empty, and
    :class:`RuntimeError` if AI Toolkit fails or writes no LoRA.
    """
    if config is None:
        config = load_config()
    trainer_cfg = _load_trainer_config(config)
    get_character(project, character_id)  # must be registered

    dataset = _dataset_dir(project, character_id)
    images = _dataset_images(dataset)
    if not images:
        raise FileNotFoundError(
            f"No training images in {dataset}; run generate_training_set first."
        )
    if len(images) < _FEW_TRAINING_IMAGES:
        print(f"[ai_toolkit] WARNING: only {len(images)} training image(s) for "
              f"{character_id}; LoRAs usually train on 10-30", flush=True)

    training_folder = project.assets_dir / "characters" / character_id / "lora"
    job = _job_name(project, character_id)
    lora_path = training_folder / job / f"{job}.safetensors"

    if lora_path.is_file() and not force:
        print(f"[ai_toolkit] {character_id}: LoRA exists, not retraining "
              f"(force=True to retrain): {lora_path}", flush=True)
    else:
        if force and (training_folder / job).exists():
            shutil.rmtree(training_folder / job)
        training_folder.mkdir(parents=True, exist_ok=True)
        job_config = _build_job_config(job, dataset, training_folder, character_id, trainer_cfg)
        config_path = training_folder / f"{job}.yaml"
        config_path.write_text(yaml.safe_dump(job_config, sort_keys=False), encoding="utf-8")
        print(f"[ai_toolkit] training {character_id} on {len(images)} image(s), "
              f"{trainer_cfg['steps']} steps", flush=True)
        _run_ai_toolkit(config_path, trainer_cfg)
        if not lora_path.is_file():
            raise RuntimeError(
                f"AI Toolkit finished but wrote no LoRA at {lora_path} "
                f"(see its output above)."
            )

    set_lora(project, "character", character_id, lora_path,
             weight=trainer_cfg["lora_weight"], trigger_word=character_id)
    return lora_path


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


def _load_trainer_config(config: dict[str, Any]) -> dict[str, Any]:
    """Extract and validate the ``ai_toolkit`` section with defaults."""
    root = cfg_get(config, "ai_toolkit.root", None)
    python_bin = cfg_get(config, "ai_toolkit.python_bin", None)
    if not root or not python_bin:
        raise RuntimeError(
            "ai_toolkit.root and ai_toolkit.python_bin must be set in "
            "lazycomics_config.yaml — they point to your Pinokio AI Toolkit "
            "install and its Python env binary."
        )
    return {
        "root": Path(root).expanduser().resolve(),
        "python_bin": Path(python_bin).expanduser().resolve(),
        "arch": cfg_get(config, "ai_toolkit.arch", _DEFAULT_ARCH),
        "name_or_path": cfg_get(config, "ai_toolkit.name_or_path", _DEFAULT_NAME_OR_PATH),
        "steps": cfg_get(config, "ai_toolkit.steps", _DEFAULT_STEPS),
        "rank": cfg_get(config, "ai_toolkit.rank", _DEFAULT_RANK),
        "learning_rate": cfg_get(config, "ai_toolkit.learning_rate", _DEFAULT_LEARNING_RATE),
        "resolutions": cfg_get(config, "ai_toolkit.resolutions", _DEFAULT_RESOLUTIONS),
        "save_every": cfg_get(config, "ai_toolkit.save_every", _DEFAULT_SAVE_EVERY),
        "quantize": cfg_get(config, "ai_toolkit.quantize", _DEFAULT_QUANTIZE),
        "low_vram": cfg_get(config, "ai_toolkit.low_vram", _DEFAULT_LOW_VRAM),
        "layer_offloading": cfg_get(config, "ai_toolkit.layer_offloading", _DEFAULT_LAYER_OFFLOADING),
        "lora_weight": cfg_get(config, "ai_toolkit.lora_weight", _DEFAULT_LORA_WEIGHT),
    }


# ---------------------------------------------------------------------------
# Job config — the AI Toolkit adapter
# ---------------------------------------------------------------------------


def _build_job_config(
    job: str,
    dataset: Path,
    training_folder: Path,
    trigger: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """The AI Toolkit job (same schema as its ``config/examples/*.yaml``).

    The output lands at ``<training_folder>/<job>/<job>.safetensors``, with
    a step-numbered checkpoint every ``save_every`` steps beside it.
    """
    return {
        "job": "extension",
        "config": {
            "name": job,
            "process": [{
                "type": "sd_trainer",
                "training_folder": str(training_folder),
                "device": "cuda:0",
                # Added to any caption that lacks it (generated captions lead with it).
                "trigger_word": trigger,
                "network": {"type": "lora", "linear": cfg["rank"], "linear_alpha": cfg["rank"]},
                "save": {
                    "dtype": "bf16",
                    "save_every": cfg["save_every"],
                    "max_step_saves_to_keep": 4,
                },
                "datasets": [{
                    "folder_path": str(dataset),
                    "caption_ext": "txt",
                    "cache_latents_to_disk": True,
                    "resolution": list(cfg["resolutions"]),
                }],
                "train": {
                    "batch_size": 1,
                    "steps": cfg["steps"],
                    "gradient_accumulation": 1,
                    "train_unet": True,
                    "train_text_encoder": False,
                    "gradient_checkpointing": True,
                    "noise_scheduler": "flowmatch",
                    "timestep_type": "weighted",
                    "optimizer": "adamw8bit",
                    "lr": cfg["learning_rate"],
                    # Encode every caption once, then drop Qwen3 from VRAM.
                    "cache_text_embeddings": True,
                    "dtype": "bf16",
                    # Samples need the text encoder and minutes each; judge
                    # the checkpoints with a golden run instead.
                    "disable_sampling": True,
                },
                "model": {
                    "arch": cfg["arch"],
                    "name_or_path": cfg["name_or_path"],
                    "quantize": cfg["quantize"],
                    "qtype": "qfloat8",
                    "quantize_te": cfg["quantize"],
                    "qtype_te": "qfloat8",
                    "low_vram": cfg["low_vram"],
                    "layer_offloading": cfg["layer_offloading"],
                    "model_kwargs": {"match_target_res": False},
                },
                "sample": {
                    "sampler": "flowmatch",
                    "sample_every": cfg["steps"],
                    "width": 1024,
                    "height": 1024,
                    "prompts": [f"{trigger}, close-up portrait, front view"],
                    "seed": 42,
                    "guidance_scale": 4,
                    "sample_steps": 25,
                },
            }],
        },
        "meta": {"name": job, "version": "1.0"},
    }


# ---------------------------------------------------------------------------
# AI Toolkit execution
# ---------------------------------------------------------------------------


def _run_ai_toolkit(config_path: Path, cfg: dict[str, Any]) -> None:
    """Run ``run.py <config>`` in AI Toolkit's env, streaming its output.

    Raises ``RuntimeError`` if it exits non-zero.
    """
    cmd = [str(cfg["python_bin"]), "run.py", str(config_path)]
    print(f"[ai_toolkit]   running: {' '.join(cmd)}", flush=True)
    start = time.perf_counter()
    result = subprocess.run(cmd, cwd=str(cfg["root"]))
    elapsed = time.perf_counter() - start
    if result.returncode != 0:
        raise RuntimeError(
            f"AI Toolkit exited with code {result.returncode} "
            f"(after {elapsed / 60:.1f} min). See output above for details."
        )
    print(f"[ai_toolkit]   done in {elapsed / 60:.1f} min", flush=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dataset_dir(project: Project, character_id: str) -> Path:
    return project.assets_dir / "characters" / character_id / "lora_dataset"


def _dataset_images(dataset: Path) -> list[Path]:
    """Training images directly in ``dataset`` (not ``rejected/`` or AI Toolkit's caches)."""
    if not dataset.is_dir():
        return []
    return sorted(p for p in dataset.iterdir()
                  if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES)


def _job_name(project: Project, character_id: str) -> str:
    """Also the LoRA's filename, which ends up in Wan2GP's shared loras
    folder: hence the project name, so two projects' LoRAs don't collide."""
    return f"lazycomics_{project.name}_{character_id}"


def _display_name(character_id: str) -> str:
    """``"GERALD"`` → ``"Gerald"``, as the refined prompts name characters."""
    return character_id.replace("_", " ").title()


def _style_prefix(project: Project) -> str:
    try:
        return get_style(project).prompt_prefix.strip()
    except FileNotFoundError:
        return ""
