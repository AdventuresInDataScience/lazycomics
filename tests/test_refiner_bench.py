"""Smoke test for tools/refiner_bench.py.

The bench is a dev tool, but it is what keeps refiner and prompt changes
honest beyond the story they were tuned on, so a broken generator or tally
should fail loudly here rather than quietly report a clean run.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))

pytest.importorskip("cbml_parser")

import refiner_bench  # noqa: E402
import lazycomics as lc  # noqa: E402
from lazycomics import llm_refiner  # noqa: E402
from lazycomics.prompt_builder import group_cue  # noqa: E402


def test_synthetic_script_is_seeded_and_covers_the_rules():
    with tempfile.TemporaryDirectory() as tmp:
        a = refiner_bench.synthetic_script(Path(tmp) / "a", seed=1, panels=16)
        b = refiner_bench.synthetic_script(Path(tmp) / "b", seed=1, panels=16)
        assert a.cbml_path.read_text() == b.cbml_path.read_text()

        lc.enrich(a)
        panels = [json.loads(p.read_text()) for p in a.enriched_dir.glob("*.json")]
        assert len(panels) == 16
        assert any(group_cue(p) for p in panels)
        assert any(len(p["characters"]) >= 3 and not group_cue(p) for p in panels)
        assert any(p["sfx_lines"] for p in panels) and any(p["dialogue_lines"] for p in panels)
        assert all(c["visual_description"] for p in panels for c in p["characters"])


def test_bench_scores_the_first_reply_and_the_retry(monkeypatch):
    # Every first reply adds a billboard (a signage break); every retry fixes it.
    monkeypatch.setattr(llm_refiner, "load_config",
                        lambda *a, **k: {"llm": {"url": "http://fake/v1", "model": "fake"}})

    def fake_llm(endpoint, headers, body):
        prompt = body["messages"][1]["content"].split("Prompt to refine:\n")[-1]
        return prompt + (", refined" if len(body["messages"]) > 2 else ", a billboard")

    monkeypatch.setattr(llm_refiner, "_call_llm", fake_llm)
    with tempfile.TemporaryDirectory() as tmp:
        project = refiner_bench.synthetic_script(Path(tmp) / "s", seed=3, panels=4)
        tally = refiner_bench.bench(project, Path(tmp) / "s.txt")
        report = (Path(tmp) / "s.txt").read_text()
    assert tally["panels"] == 4
    assert tally["signage"] == tally["retried"] == tally["fixed by the retry"] == 4
    assert tally["clean first time"] == tally["kept the original"] == 0
    assert report.count("breaks signage") == 4 and "refined after a retry" in report
