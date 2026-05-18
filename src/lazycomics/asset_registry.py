"""Asset registration (plan §13.3).

Assets live under ``<project>/assets/``:

    assets/
    ├── style/                    # one per project
    │   ├── references/
    │   └── meta.json
    ├── characters/
    │   └── NOVA/
    │       ├── references/
    │       └── meta.json
    └── locations/
        └── warehouse_night/
            ├── references/
            └── meta.json

The filesystem layout IS the registry — no manifest sync. ``meta.json``
holds description + LoRA info; ``references/`` holds the user's source
images (copied in by the register_* functions).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from lazycomics.models import (
    CharacterRef,
    LocationAsset,
    LoRAConfig,
    Project,
    StyleAsset,
)

__all__ = [
    "register_character",
    "register_style",
    "register_location",
    "set_lora",
    "get_character",
    "get_style",
    "get_location",
]


_DEFAULT_WEIGHTS = {"character": 0.7, "style": 0.8, "location": 0.5}


# ---------------------------------------------------------------------------
# Registration (writes)
# ---------------------------------------------------------------------------


def register_character(
    project: Project,
    identifier: str,
    description: str = "",
    reference_images: list[str | Path] | None = None,
    lora_path: str | Path | None = None,
    lora_weight: float = 0.7,
) -> CharacterRef:
    """Register a character. Copies refs into the project, writes meta.json.

    Idempotent: re-registering the same identifier replaces the previous
    entry (references/ is wiped and rewritten). Raises
    :class:`FileNotFoundError` if any source image is missing.
    """
    asset_dir = project.assets_dir / "characters" / identifier
    _write_asset(asset_dir, reference_images or [], {
        "identifier": identifier,
        "description": description,
        "lora_path": str(lora_path) if lora_path else None,
        "lora_weight": lora_weight if lora_path else None,
        "trigger_word": None,
    })
    return get_character(project, identifier)


def register_style(
    project: Project,
    preset: str = "custom",
    prompt_prefix: str = "",
    reference_images: list[str | Path] | None = None,
    lora_path: str | Path | None = None,
    lora_weight: float = 0.8,
) -> StyleAsset:
    """Register the project's style (one per project, lives at ``assets/style/``)."""
    asset_dir = project.assets_dir / "style"
    _write_asset(asset_dir, reference_images or [], {
        "preset": preset,
        "prompt_prefix": prompt_prefix,
        "lora_path": str(lora_path) if lora_path else None,
        "lora_weight": lora_weight if lora_path else None,
        "trigger_word": None,
    })
    return get_style(project)


def register_location(
    project: Project,
    identifier: str,
    description: str = "",
    reference_images: list[str | Path] | None = None,
    lora_path: str | Path | None = None,
    lora_weight: float = 0.5,
) -> LocationAsset:
    """Register a location."""
    asset_dir = project.assets_dir / "locations" / identifier
    _write_asset(asset_dir, reference_images or [], {
        "identifier": identifier,
        "description": description,
        "lora_path": str(lora_path) if lora_path else None,
        "lora_weight": lora_weight if lora_path else None,
        "trigger_word": None,
    })
    return get_location(project, identifier)


def set_lora(
    project: Project,
    asset_type: str,
    identifier: str | None,
    lora_path: str | Path,
    weight: float | None = None,
    trigger_word: str | None = None,
) -> None:
    """Attach or update a LoRA on an already-registered asset.

    ``asset_type`` is ``"character"``, ``"style"``, or ``"location"``. For
    ``"style"`` pass ``identifier=None``. Raises :class:`FileNotFoundError`
    if the asset isn't registered yet (register it first).

    ``weight=None`` keeps any previously-set weight, or applies the default
    for that asset_type if none was set.
    """
    asset_dir = _resolve_asset_dir(project, asset_type, identifier)
    meta_path = asset_dir / "meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(
            f"Asset not registered: {asset_type}/{identifier or 'style'}"
        )

    with meta_path.open(encoding="utf-8") as f:
        meta = json.load(f)

    meta["lora_path"] = str(lora_path)
    if weight is not None:
        meta["lora_weight"] = weight
    elif meta.get("lora_weight") is None:
        meta["lora_weight"] = _DEFAULT_WEIGHTS[asset_type]
    if trigger_word is not None:
        meta["trigger_word"] = trigger_word

    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def get_character(project: Project, identifier: str) -> CharacterRef:
    """Read a registered character. Raises :class:`FileNotFoundError` if absent."""
    asset_dir = project.assets_dir / "characters" / identifier
    meta = _read_meta(asset_dir)
    return CharacterRef(
        identifier=meta["identifier"],
        visual_description=meta.get("description", ""),
        reference_images=_list_refs(asset_dir),
        lora=_build_lora(meta),
    )


def get_style(project: Project) -> StyleAsset:
    """Read the project's style. Raises :class:`FileNotFoundError` if not registered."""
    asset_dir = project.assets_dir / "style"
    meta = _read_meta(asset_dir)
    return StyleAsset(
        preset=meta.get("preset", "custom"),
        prompt_prefix=meta.get("prompt_prefix", ""),
        reference_images=_list_refs(asset_dir),
        lora=_build_lora(meta),
    )


def get_location(project: Project, identifier: str) -> LocationAsset:
    """Read a registered location. Raises :class:`FileNotFoundError` if absent."""
    asset_dir = project.assets_dir / "locations" / identifier
    meta = _read_meta(asset_dir)
    return LocationAsset(
        identifier=meta["identifier"],
        description=meta.get("description", ""),
        reference_images=_list_refs(asset_dir),
        lora=_build_lora(meta),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_asset_dir(project: Project, asset_type: str, identifier: str | None) -> Path:
    if asset_type == "style":
        return project.assets_dir / "style"
    if asset_type == "character":
        if not identifier:
            raise ValueError("character requires identifier")
        return project.assets_dir / "characters" / identifier
    if asset_type == "location":
        if not identifier:
            raise ValueError("location requires identifier")
        return project.assets_dir / "locations" / identifier
    raise ValueError(f"unknown asset_type: {asset_type!r}")


def _write_asset(asset_dir: Path, reference_images: list[str | Path], meta: dict[str, Any]) -> None:
    """Create asset_dir, copy in references, write meta.json. Idempotent."""
    sources = [Path(p) for p in reference_images]
    # Validate everything before touching disk so we don't half-create on error.
    for src in sources:
        if not src.is_file():
            raise FileNotFoundError(f"Reference image not found: {src}")

    asset_dir.mkdir(parents=True, exist_ok=True)
    refs_dir = asset_dir / "references"
    if refs_dir.exists():
        shutil.rmtree(refs_dir)
    refs_dir.mkdir()

    for src in sources:
        shutil.copy(src, refs_dir / src.name)

    with (asset_dir / "meta.json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def _read_meta(asset_dir: Path) -> dict[str, Any]:
    meta_path = asset_dir / "meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"No meta.json at {meta_path}")
    with meta_path.open(encoding="utf-8") as f:
        return json.load(f)


def _list_refs(asset_dir: Path) -> list[Path]:
    refs_dir = asset_dir / "references"
    if not refs_dir.is_dir():
        return []
    return sorted(p for p in refs_dir.iterdir() if p.is_file())


def _build_lora(meta: dict[str, Any]) -> LoRAConfig | None:
    lp = meta.get("lora_path")
    if not lp:
        return None
    return LoRAConfig(
        path=Path(lp),
        weight=meta.get("lora_weight") or 0.7,
        trigger_word=meta.get("trigger_word"),
    )
