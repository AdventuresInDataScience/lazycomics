"""lazycomics — composable helpers for CBML → .cbz comic generation.

Public API is intentionally flat. Functions are added as each step lands.
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

from lazycomics.asset_registry import (
    get_character,
    get_location,
    get_style,
    register_character,
    register_location,
    register_style,
    set_lora,
)
from lazycomics.assembler import assemble_pages
from lazycomics.config import cfg_get, load_config
from lazycomics.enricher import enrich
from lazycomics.exporter import export_cbz
from lazycomics.llm_refiner import refine_prompts_with_llm
from lazycomics.wan2gp_bridge import generate_panels
from lazycomics.upscaler import upscale_pages
from lazycomics.models import (
    BubbleRegion,
    CaptionBox,
    CharacterRef,
    DialogueLine,
    LocationAsset,
    LoRAConfig,
    PageLayout,
    PanelGenerationRequest,
    Project,
    Sfx,
    StyleAsset,
)
from lazycomics.project import create_project, load_project
from lazycomics.prompt_builder import build_prompts
from lazycomics.ref_preparer import prepare_references
from lazycomics.text_renderer import render_text

__all__ = [
    # models
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
    # config
    "cfg_get",
    "load_config",
    # project
    "create_project",
    "load_project",
    # asset_registry
    "get_character",
    "get_location",
    "get_style",
    "register_character",
    "register_location",
    "register_style",
    "set_lora",
    # enricher
    "enrich",
    # prompt_builder
    "build_prompts",
    # llm_refiner
    "refine_prompts_with_llm",
    # ref_preparer
    "prepare_references",
    # text_renderer
    "render_text",
    # assembler
    "assemble_pages",
    # wan2gp_bridge
    "generate_panels",
    # upscaler
    "upscale_pages",
    # exporter
    "export_cbz",
]

try:
    __version__ = _pkg_version("lazycomics")
except PackageNotFoundError:
    # Running from source without an installed dist (rare; tests / direct
    # PYTHONPATH usage). Keep importing usable rather than crashing.
    __version__ = "0.0.0+unknown"
