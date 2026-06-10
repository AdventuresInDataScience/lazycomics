"""Architecture timing profiler (dev utility).

Times :func:`wan2gp_bridge.generate_panels` for two or more Wan2GP
architectures over the same project, so you can compare e.g. Flux 2 Klein
against full Flux Dev generation time on a representative page before
committing to a model switch (§ 14.x Tier-3 option).

Because it actually generates panels, it needs a working Wan2GP install and a
GPU — it runs on the host, not in CI. The timing/aggregation logic is kept
separate from the Wan2GP call (injected as ``runner``) so it is unit-testable
without a GPU.

Typical host workflow::

    create_project -> enrich -> build_prompts -> prepare_references
        -> profile_architectures(project, ["flux2_klein_9b", "<flux_dev>"])
"""

from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Any, Callable

from lazycomics.models import Project

__all__ = ["profile_architectures", "format_profile"]


def profile_architectures(
    project: Project,
    architectures: list[str],
    *,
    config: dict[str, Any] | None = None,
    runner: Callable[..., dict[str, Path]] | None = None,
    force: bool = True,
) -> dict[str, dict[str, Any]]:
    """Time panel generation for each architecture over ``project``.

    For each entry in ``architectures``, a *deep copy* of ``config`` with
    ``wan2gp.architecture`` overridden is handed to ``runner`` (defaults to
    :func:`wan2gp_bridge.generate_panels`), wall-clock timed, and recorded.
    The original ``config`` is never mutated.

    ``force=True`` (default) regenerates every panel each run so timings are
    not skewed by skip-if-exists. Returns
    ``{architecture: {"seconds": float, "panels": int}}`` in input order.
    """
    if config is None:
        from lazycomics.config import load_config  # lazy
        config = load_config()
    if runner is None:
        # Lazy import — pulls the bridge's dependency chain only when actually
        # generating, so unit tests can inject a fake runner cheaply.
        from lazycomics.wan2gp_bridge import generate_panels
        runner = generate_panels

    results: dict[str, dict[str, Any]] = {}
    for arch in architectures:
        cfg = copy.deepcopy(config)
        cfg.setdefault("wan2gp", {})
        if not isinstance(cfg["wan2gp"], dict):
            cfg["wan2gp"] = {}
        cfg["wan2gp"]["architecture"] = arch

        start = time.perf_counter()
        produced = runner(project, force=force, config=cfg)
        elapsed = time.perf_counter() - start

        results[arch] = {"seconds": elapsed, "panels": len(produced or {})}
    return results


def format_profile(results: dict[str, dict[str, Any]]) -> str:
    """Render a profile dict as a small aligned table (plus a speedup note)."""
    if not results:
        return "(no architectures profiled)"

    width = max(len("architecture"), max(len(a) for a in results))
    header = f"{'architecture'.ljust(width)}   {'panels':>6}   {'seconds':>9}   {'s/panel':>8}"
    lines = [header, "-" * len(header)]
    for arch, r in results.items():
        secs = float(r.get("seconds", 0.0) or 0.0)
        panels = int(r.get("panels", 0) or 0)
        per = secs / panels if panels else 0.0
        lines.append(
            f"{arch.ljust(width)}   {panels:>6}   {secs:>9.1f}   {per:>8.2f}"
        )

    # Relative note: slowest vs fastest, when we have 2+ timed architectures.
    timed = {a: float(r.get("seconds", 0.0) or 0.0) for a, r in results.items()}
    timed = {a: s for a, s in timed.items() if s > 0}
    if len(timed) >= 2:
        fastest = min(timed, key=timed.get)
        slowest = max(timed, key=timed.get)
        if timed[fastest] > 0:
            ratio = timed[slowest] / timed[fastest]
            lines.append("")
            lines.append(f"{slowest} is {ratio:.2f}x the time of {fastest}")
    return "\n".join(lines)
