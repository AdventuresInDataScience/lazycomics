"""CBZ export — bundle ``pages/page_*.png`` into a ``.cbz`` archive.

A ``.cbz`` is just a ZIP archive of images with a different file
extension. Every comic reader (CDisplayEx, Calibre, YACReader, Kavita,
Komga, etc.) treats them this way. We use ``ZIP_STORED`` because the
PNG images are already deflate-compressed internally; re-deflating in
ZIP wastes CPU and yields essentially the same file size.

Output goes to ``<project>/output/<name>.cbz`` and the function returns
that path. Existing archives are not overwritten unless ``force=True``.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from lazycomics.models import Project

__all__ = ["export_cbz"]


def export_cbz(
    project: Project,
    output_filename: str | None = None,
    *,
    force: bool = False,
    prefer_upscaled: bool = True,
) -> Path:
    """Bundle the project's page PNGs into a ``.cbz`` archive.

    Parameters
    ----------
    project
        The lazycomics project.
    output_filename
        Name of the output file. Defaults to ``"<project.name>.cbz"``.
        A missing ``.cbz`` extension is appended automatically. Path
        separators are not stripped — pass a bare filename, not a path.
    force
        If ``True``, overwrite an existing archive. If ``False``
        (default) and the archive already exists, this returns the
        existing path without rewriting.
    prefer_upscaled
        If ``True`` (default) and ``<project>/pages_upscaled/`` contains
        ``page_*.png`` files, those are bundled instead of ``pages/`` —
        so a run that includes the upscale stage ships the upscaled
        pages. Set ``False`` to always bundle the un-upscaled ``pages/``.

    Returns
    -------
    pathlib.Path
        Path to the (now-existing) ``.cbz`` file.

    Raises
    ------
    FileNotFoundError
        If no ``page_*.png`` files are found to bundle — there's nothing
        to archive.
    """
    filename = output_filename or f"{project.name}.cbz"
    if not filename.lower().endswith(".cbz"):
        filename = f"{filename}.cbz"
    output_path = project.output_dir / filename

    if output_path.is_file() and not force:
        return output_path

    pages = _select_pages(project, prefer_upscaled)
    if not pages:
        raise FileNotFoundError(
            "no pages to export — expected page_*.png files in "
            f"{project.pages_dir}"
            + (f" or {project.pages_upscaled_dir}" if prefer_upscaled else "")
        )

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for page_path in pages:
            zf.write(page_path, arcname=page_path.name)

    return output_path


def _select_pages(project: Project, prefer_upscaled: bool) -> list[Path]:
    """Return the page PNGs to bundle: upscaled if present and preferred."""
    if prefer_upscaled:
        upscaled = sorted(project.pages_upscaled_dir.glob("page_*.png"))
        if upscaled:
            return upscaled
    return sorted(project.pages_dir.glob("page_*.png"))
