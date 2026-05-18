"""Data model for lazycomics (plan §13.1).

Plain dataclasses. No behaviour, no serialization — the modules that produce
or consume these (enricher, project, etc.) handle their own JSON conversion
at the point of use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "BubbleRegion",
    "CaptionBox",
    "CharacterRef",
    "DialogueLine",
    "LocationAsset",
    "LoRAConfig",
    "PageLayout",
    "PanelGenerationRequest",
    "Project",
    "Sfx",
    "StyleAsset",
]


@dataclass
class LoRAConfig:
    """A LoRA adapter with its blend weight."""

    path: Path
    weight: float
    trigger_word: str | None = None


@dataclass
class DialogueLine:
    """One speech/thought/shout/whisper line on a panel."""

    character: str
    text: str
    bubble_type: str = "speech"  # "speech" | "thought" | "shout" | "whisper"


@dataclass
class CaptionBox:
    """A narrator/caption box rendered onto a panel."""

    text: str
    bg_color: str = "#fff8c8"
    text_color: str = "#111111"
    position: str = "top-left"


@dataclass
class Sfx:
    """A sound-effect overlay — free-floating styled text, no background box.

    Per CBML v1.1, ``position`` is one of the six caption corner anchors plus
    ``center`` (SFX that float over the action).
    """

    text: str
    color: str = "#000000"
    position: str = "center"


@dataclass
class BubbleRegion:
    """Reserved area for a speech bubble, as fractions of the panel."""

    character: str
    x_frac: float
    y_frac: float
    width_frac: float
    height_frac: float


@dataclass
class CharacterRef:
    """Registered character: description, ref images, optional LoRA."""

    identifier: str
    visual_description: str = ""
    reference_images: list[Path] = field(default_factory=list)
    lora: LoRAConfig | None = None


@dataclass
class StyleAsset:
    """The comic's visual style: preset name, ref images, optional LoRA."""

    preset: str = "custom"
    prompt_prefix: str = ""
    reference_images: list[Path] = field(default_factory=list)
    lora: LoRAConfig | None = None


@dataclass
class LocationAsset:
    """A registered location: description, ref images, optional LoRA."""

    identifier: str
    description: str = ""
    reference_images: list[Path] = field(default_factory=list)
    lora: LoRAConfig | None = None


@dataclass
class Project:
    """Filesystem layout of a lazycomics project."""

    name: str
    base_dir: Path
    cbml_path: Path
    assets_dir: Path
    enriched_dir: Path
    prompts_dir: Path
    refs_prepared_dir: Path
    panels_dir: Path
    panels_text_dir: Path
    pages_dir: Path
    pages_upscaled_dir: Path
    output_dir: Path
    manifest_path: Path


@dataclass
class PanelGenerationRequest:
    """One panel's mechanical decisions + prompt material.

    ``pixel_rect`` is ``(x, y, width, height)`` in page-pixel coordinates.
    ``bleed_edges`` holds any of ``{"top", "right", "bottom", "left"}``.
    ``char_generation_strategy`` is ``"single"`` or ``"multi_inpaint"``.

    Fields marked *populated* are set by the enricher today. Fields marked
    *deferred* are on the contract but not yet consumed — they'll be
    populated when a consumer arrives, or reviewed for removal once Phase 2
    stabilises.
    """

    panel_id: str
    page_index: int
    panel_index: int
    aspect_ratio: float = 1.0
    pixel_rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # deferred: populate when a post-generation stage needs per-panel coords
    bleed_edges: set[str] = field(default_factory=set)

    loc_identifier: str | None = None
    loc_description: str = ""
    loc_reference_images: list[Path] = field(default_factory=list)

    characters: list[CharacterRef] = field(default_factory=list)
    primary_character: str | None = None                           # populated: enricher (wan2gp_bridge consumer)
    char_generation_strategy: str = "single"                       # populated: enricher (wan2gp_bridge consumer)
    inpaint_order: list[str] = field(default_factory=list)         # populated: enricher (wan2gp_bridge consumer)
    occlusion_order: list[str] = field(default_factory=list)       # deferred: populate when shot-hint depth-cue parsing lands

    shot_hint: str = ""
    mood: str | None = None
    action: str = ""

    dialogue_lines: list[DialogueLine] = field(default_factory=list)
    caption_boxes: list[CaptionBox] = field(default_factory=list)
    sfx_lines: list[Sfx] = field(default_factory=list)
    bubble_layout: list[BubbleRegion] = field(default_factory=list)

    composition_flags: dict[str, Any] = field(default_factory=dict)  # deferred: populate when prompt_builder grows to consume them
    panel_context: dict[str, Any] = field(default_factory=dict)      # deferred: populate when a consumer arrives

    pose_ref: Path | None = None                                     # deferred: populate when ControlNet integration lands
    lora_stack: list[LoRAConfig] = field(default_factory=list)       # deferred: populate when ai_toolkit_bridge lands

    # The pipeline caches prompts as <id>.txt / <id>.neg.txt files. These
    # in-dataclass slots are for the generation bridge to use when it stops
    # re-reading those files on every panel.
    prompt: str | None = None
    negative_prompt: str | None = None


@dataclass
class PageLayout:
    """One page's grid + the panel requests that fill it.

    Currently UNUSED — no consumer. The pipeline reads page geometry
    directly off the parsed CBML inside the assembler, and the enricher
    derives per-panel aspect itself. Kept on the contract: populate if a
    consumer arises (e.g. a shared page-geometry contract replacing the
    ad-hoc ``_grid_dims`` helpers), otherwise review for deletion once
    Phase 2 stabilises.
    """

    page_index: int
    grid_cols: int
    grid_rows: int
    page_width_px: int
    page_height_px: int
    gutter_px: int = 20
    panels: list[PanelGenerationRequest] = field(default_factory=list)
