"""Project management (plan §13.2).

* ``create_project`` — make the directory tree, copy the CBML, write a manifest.
* ``load_project`` — read an existing project's manifest, return a Project.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from lazycomics.models import Project

__all__ = ["create_project", "load_project"]


_MANIFEST_NAME = "manifest.json"


def create_project(
    name: str,
    cbml_path: str | Path,
    base_dir: str | Path = "./projects",
) -> Project:
    """Create a new project at ``<base_dir>/<name>``.

    Makes the directory tree, copies ``cbml_path`` in as ``source.cbml``,
    writes an initial ``manifest.json``, and returns the :class:`Project`.

    Raises :class:`FileExistsError` if the project directory already exists
    (use :func:`load_project` to open an existing one). Raises
    :class:`FileNotFoundError` if ``cbml_path`` doesn't exist.
    """
    base = Path(base_dir).expanduser().resolve()
    project_dir = base / name

    if project_dir.exists():
        raise FileExistsError(f"Project directory already exists: {project_dir}")

    cbml_src = Path(cbml_path).expanduser().resolve()
    if not cbml_src.is_file():
        raise FileNotFoundError(f"CBML file not found: {cbml_src}")

    project_dir.mkdir(parents=True)
    for sub in ("assets", "enriched", "prompts", "refs_prepared",
                "panels", "panels_text", "pages", "pages_upscaled", "output"):
        (project_dir / sub).mkdir()

    shutil.copy(cbml_src, project_dir / "source.cbml")

    project = _build_project(name, project_dir)
    with project.manifest_path.open("w", encoding="utf-8") as f:
        json.dump({"name": name}, f, indent=2)
    return project


def load_project(project_dir: str | Path) -> Project:
    """Load an existing project from its root directory.

    Reads ``manifest.json`` and reconstructs the :class:`Project`. Raises
    :class:`FileNotFoundError` if no manifest is present.
    """
    project_dir = Path(project_dir).expanduser().resolve()
    manifest_path = project_dir / _MANIFEST_NAME

    if not manifest_path.is_file():
        raise FileNotFoundError(f"No manifest at {manifest_path}")

    with manifest_path.open(encoding="utf-8") as f:
        data = json.load(f)

    return _build_project(data["name"], project_dir)


def _build_project(name: str, project_dir: Path) -> Project:
    """Construct a Project dataclass with all paths under ``project_dir``."""
    return Project(
        name=name,
        base_dir=project_dir,
        cbml_path=project_dir / "source.cbml",
        assets_dir=project_dir / "assets",
        enriched_dir=project_dir / "enriched",
        prompts_dir=project_dir / "prompts",
        refs_prepared_dir=project_dir / "refs_prepared",
        panels_dir=project_dir / "panels",
        panels_text_dir=project_dir / "panels_text",
        pages_dir=project_dir / "pages",
        pages_upscaled_dir=project_dir / "pages_upscaled",
        output_dir=project_dir / "output",
        manifest_path=project_dir / _MANIFEST_NAME,
    )
