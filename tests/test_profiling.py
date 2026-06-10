"""Unit tests for lazycomics.profiling (timing/aggregation logic only).

The real Wan2GP call is injected as ``runner`` so these run without a GPU.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lazycomics.profiling import format_profile, profile_architectures  # noqa: E402


def _fake_project():
    return SimpleNamespace(name="bench")


def test_profile_times_each_architecture_in_order():
    calls = []

    def runner(project, *, force, config):
        calls.append((config["wan2gp"]["architecture"], force))
        # Pretend we produced 6 panels.
        return {f"page_1_panel_{i}": Path(f"/x/{i}.png") for i in range(1, 7)}

    results = profile_architectures(
        _fake_project(), ["flux2_klein_9b", "flux"],
        config={"wan2gp": {"architecture": "ignored"}}, runner=runner,
    )

    assert list(results.keys()) == ["flux2_klein_9b", "flux"]
    for arch in ("flux2_klein_9b", "flux"):
        assert results[arch]["panels"] == 6
        assert isinstance(results[arch]["seconds"], float)
        assert results[arch]["seconds"] >= 0.0

    # Runner got each architecture, with force=True (so skip-if-exists can't
    # skew the timing).
    assert calls == [("flux2_klein_9b", True), ("flux", True)]


def test_profile_does_not_mutate_caller_config():
    original = {"wan2gp": {"architecture": "flux2_klein_9b", "seed": 42}}

    def runner(project, *, force, config):
        return {}

    profile_architectures(_fake_project(), ["flux"], config=original, runner=runner)

    # The caller's config is untouched (deep-copied per architecture).
    assert original["wan2gp"]["architecture"] == "flux2_klein_9b"
    assert original["wan2gp"]["seed"] == 42


def test_profile_handles_missing_wan2gp_section():
    seen = {}

    def runner(project, *, force, config):
        seen["arch"] = config["wan2gp"]["architecture"]
        return {"page_1_panel_1": Path("/x/1.png")}

    results = profile_architectures(
        _fake_project(), ["flux"], config={}, runner=runner,
    )
    assert seen["arch"] == "flux"
    assert results["flux"]["panels"] == 1


def test_profile_respects_force_false():
    seen = {}

    def runner(project, *, force, config):
        seen["force"] = force
        return {}

    profile_architectures(
        _fake_project(), ["flux"], config={}, runner=runner, force=False,
    )
    assert seen["force"] is False


def test_format_profile_contains_arch_names_and_speedup():
    results = {
        "flux2_klein_9b": {"seconds": 60.0, "panels": 6},
        "flux": {"seconds": 180.0, "panels": 6},
    }
    out = format_profile(results)
    assert "flux2_klein_9b" in out
    assert "flux" in out
    assert "s/panel" in out
    # 180 / 60 = 3.00x, slowest (flux) vs fastest (klein).
    assert "3.00x" in out


def test_format_profile_empty():
    assert format_profile({}) == "(no architectures profiled)"


def test_format_profile_single_arch_no_speedup_line():
    out = format_profile({"flux2_klein_9b": {"seconds": 60.0, "panels": 6}})
    assert "flux2_klein_9b" in out
    assert "x the time of" not in out
