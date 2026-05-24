"""Run a subset of test modules — faster iteration than the full suite.

Usage: python tests/run_subset.py <module_stem> [<module_stem> ...]
e.g.   python tests/run_subset.py text_renderer wan2gp_bridge ref_preparer
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import traceback
import types
from pathlib import Path


class _RaisesCtx:
    def __init__(self, expected):
        self.expected = expected

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError(f"Expected {self.expected.__name__} but no exception was raised")
        return issubclass(exc_type, self.expected)


pytest_shim = types.ModuleType("pytest")
pytest_shim.raises = lambda exc: _RaisesCtx(exc)  # type: ignore[attr-defined]
sys.modules["pytest"] = pytest_shim


HERE = Path(__file__).parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


stems = sys.argv[1:] or ["text_renderer", "wan2gp_bridge", "ref_preparer", "config"]
test_files = [HERE / f"test_{stem}.py" for stem in stems]
missing = [p for p in test_files if not p.is_file()]
if missing:
    print(f"missing: {missing}")
    sys.exit(2)

modules = [_load(p) for p in test_files]

tests: list[tuple[str, callable]] = []
for mod in modules:
    for name in dir(mod):
        if name.startswith("test_") and callable(getattr(mod, name)):
            tests.append((f"{mod.__name__}.{name}", getattr(mod, name)))
tests.sort()

passed = 0
failed = 0
failures: list[tuple[str, str]] = []

for name, fn in tests:
    try:
        fn()
    except Exception:
        failed += 1
        failures.append((name, traceback.format_exc()))
        print(f"FAIL  {name}")
    else:
        passed += 1
        print(f"ok    {name}")

print()
print(f"{passed} passed, {failed} failed (out of {len(tests)})")

if failures:
    print("\n--- failures ---")
    for name, tb in failures:
        print(f"\n>>> {name}\n{tb}")
    sys.exit(1)
