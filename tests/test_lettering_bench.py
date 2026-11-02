"""Smoke test for tools/lettering_bench.py's synthetic comics.

The bench is a dev tool, but its synthetic set is what keeps lettering
changes honest beyond the story they were tuned on, so a broken generator
should fail loudly here rather than quietly letter nothing.
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

import lettering_bench  # noqa: E402
from lazycomics import review  # noqa: E402


def test_synthetic_comic_is_seeded_and_letters_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        a = lettering_bench.synthetic_comic(Path(tmp) / "a", seed=7, pages=2)
        b = lettering_bench.synthetic_comic(Path(tmp) / "b", seed=7, pages=2)
        assert a.cbml_path.read_text().splitlines()[1:] == b.cbml_path.read_text().splitlines()[1:]

        pids = sorted(p.stem for p in a.enriched_dir.glob("*.json"))
        assert pids and all((a.panels_dir / f"{p}.png").is_file() for p in pids)
        for pid in pids:
            data = json.loads((a.enriched_dir / f"{pid}.json").read_text())
            # Faces and who-is-who are given, keyed to this exact image.
            assert set(data["actual_character_regions"]) <= {c["character"] for c in data["bubble_layout"]}
            assert all(r in data["detected_faces"] for r in data["actual_character_regions"].values())

        lettering_bench.reletter(a, {"assembly": {"page_height_px": 600}})
        for pid in pids:
            assert not [i for i in review.check_panel(a, pid) if i.kind in ("record", "missing")]
