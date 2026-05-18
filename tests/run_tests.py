"""Run tests/test_models.py without pytest installed.

Shims ``pytest.raises`` with a stdlib equivalent, imports the test module,
invokes every ``test_*`` callable, and reports pass/fail.
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import traceback
import types
from pathlib import Path


# --- pytest shim ----------------------------------------------------------
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


# --- load the test module -------------------------------------------------
HERE = Path(__file__).parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


test_files = sorted(HERE.glob("test_*.py"))
modules = [_load(p) for p in test_files]


# --- run ------------------------------------------------------------------
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
