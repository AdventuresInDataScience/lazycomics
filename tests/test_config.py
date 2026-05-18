"""Tests for lazycomics.config."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics.config import cfg_get, load_config  # noqa: E402


# ---------------------------------------------------------------------------
# Test environment: redirect cwd + home so we never touch the real fs.
# ---------------------------------------------------------------------------


class _Env:
    def __init__(self):
        self.cwd = tempfile.TemporaryDirectory()
        self.home = tempfile.TemporaryDirectory()
        self._orig_cwd = Path.cwd
        self._orig_home = Path.home
        Path.cwd = staticmethod(lambda: Path(self.cwd.name))  # type: ignore[method-assign]
        Path.home = staticmethod(lambda: Path(self.home.name))  # type: ignore[method-assign]

    def write_cwd(self, body: str):
        (Path(self.cwd.name) / "lazycomics_config.yaml").write_text(body)

    def write_home(self, body: str):
        (Path(self.home.name) / ".lazycomics_config.yaml").write_text(body)

    def close(self):
        Path.cwd = self._orig_cwd  # type: ignore[method-assign]
        Path.home = self._orig_home  # type: ignore[method-assign]
        self.cwd.cleanup()
        self.home.cleanup()


def _with_env(fn):
    def wrapper():
        env = _Env()
        try:
            fn(env)
        finally:
            env.close()
    wrapper.__name__ = fn.__name__
    return wrapper


SAMPLE = """\
wan2gp:
  model: flux2-klein-9b
  default_steps: 4
llm:
  url: http://localhost:11434/v1
"""


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


@_with_env
def test_no_file_returns_empty_dict(env):
    assert load_config() == {}


@_with_env
def test_reads_cwd_file(env):
    env.write_cwd(SAMPLE)
    cfg = load_config()
    assert cfg["wan2gp"]["model"] == "flux2-klein-9b"


@_with_env
def test_reads_home_file(env):
    env.write_home(SAMPLE)
    cfg = load_config()
    assert cfg["llm"]["url"] == "http://localhost:11434/v1"


@_with_env
def test_cwd_beats_home(env):
    env.write_home("wan2gp:\n  model: home\n")
    env.write_cwd("wan2gp:\n  model: cwd\n")
    assert load_config()["wan2gp"]["model"] == "cwd"


@_with_env
def test_explicit_path_wins(env):
    env.write_cwd("wan2gp:\n  model: cwd\n")
    explicit = Path(env.cwd.name) / "other.yaml"
    explicit.write_text("wan2gp:\n  model: explicit\n")
    assert load_config(explicit)["wan2gp"]["model"] == "explicit"


@_with_env
def test_explicit_missing_raises(env):
    try:
        load_config(Path(env.cwd.name) / "nope.yaml")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Expected FileNotFoundError")


@_with_env
def test_empty_file_returns_empty_dict(env):
    env.write_cwd("")
    assert load_config() == {}


# ---------------------------------------------------------------------------
# cfg_get
# ---------------------------------------------------------------------------


def test_cfg_get_nested():
    cfg = {"wan2gp": {"model": "klein", "steps": 4}}
    assert cfg_get(cfg, "wan2gp.model") == "klein"
    assert cfg_get(cfg, "wan2gp.steps") == 4


def test_cfg_get_missing_returns_default():
    assert cfg_get({}, "wan2gp.steps", 4) == 4
    assert cfg_get({"wan2gp": {}}, "wan2gp.model", "fallback") == "fallback"


def test_cfg_get_missing_no_default_raises():
    try:
        cfg_get({}, "wan2gp.steps")
    except KeyError:
        pass
    else:
        raise AssertionError("Expected KeyError")


def test_cfg_get_default_none_returns_none():
    # The sentinel is what makes this work — without it, default=None would
    # be indistinguishable from "no default supplied" and we'd raise.
    assert cfg_get({}, "x", None) is None


def test_cfg_get_falsy_values_returned():
    cfg = {"a": 0, "b": False, "c": ""}
    assert cfg_get(cfg, "a", 99) == 0
    assert cfg_get(cfg, "b", True) is False
    assert cfg_get(cfg, "c", "default") == ""


def test_cfg_get_intermediate_non_dict_returns_default():
    assert cfg_get({"wan2gp": "scalar"}, "wan2gp.model", "fb") == "fb"


# ---------------------------------------------------------------------------
# Rot-prevention: the shipped lazycomics_config.yaml must define every key
# any module currently reads via cfg_get. When a new config key is added
# anywhere in the package, this list AND the YAML must be updated. The
# test fails loudly otherwise.
# ---------------------------------------------------------------------------


_REPO_CONFIG = Path(__file__).parent.parent / "lazycomics_config.yaml"

# Every dotted key the codebase currently passes to cfg_get(). Keep in
# sync with the modules listed in the comment block of the YAML itself.
_EXPECTED_KEYS = [
    # assembler.py
    "assembly.page_height_px",
    "assembly.gutter_px",
    "assembly.bg_color",
    "assembly.stretch_tolerance",
    # llm_refiner.py
    "llm.url",
    "llm.model",
    "llm.api_key",
    "llm.system_prompt",
]


def test_repo_config_file_exists():
    assert _REPO_CONFIG.is_file(), (
        f"Sample config missing at {_REPO_CONFIG}. The repo ships a "
        "pre-populated lazycomics_config.yaml so devs see every available "
        "knob without copying a template."
    )


def test_repo_config_defines_all_known_keys():
    """Every key cfg_get() reads anywhere in the package must be present.

    A key is 'present' if its dotted path resolves to *anything*, including
    explicit ``null`` (which is how optional knobs like ``llm.api_key`` are
    documented). It is *not* present if any segment of the path is missing
    from the YAML — that's the failure this test catches.
    """
    cfg = load_config(_REPO_CONFIG)
    sentinel = object()
    missing = [k for k in _EXPECTED_KEYS if cfg_get(cfg, k, sentinel) is sentinel]
    assert not missing, (
        f"lazycomics_config.yaml is missing keys read by the codebase: "
        f"{missing}. Add them to the YAML (and add a comment) when you "
        f"add the cfg_get call."
    )


def test_repo_config_assembly_defaults_match_module_defaults():
    """Sample values for ``assembly.*`` must equal the hardcoded fallbacks.

    The promise of the sample config is "edit me to override; absent file
    behaves identically to the file as shipped". If these drift, that
    promise breaks silently.
    """
    from lazycomics import assembler  # noqa: PLC0415

    cfg = load_config(_REPO_CONFIG)
    assert cfg_get(cfg, "assembly.page_height_px") == assembler._DEFAULT_PAGE_HEIGHT_PX
    assert cfg_get(cfg, "assembly.gutter_px") == assembler._DEFAULT_GUTTER_PX
    assert cfg_get(cfg, "assembly.bg_color") == assembler._DEFAULT_BG_COLOR
    assert cfg_get(cfg, "assembly.stretch_tolerance") == assembler._DEFAULT_STRETCH_TOLERANCE
