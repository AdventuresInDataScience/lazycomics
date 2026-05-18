"""Tests for lazycomics.llm_refiner.

Tests monkeypatch ``llm_refiner._call_llm`` to avoid real HTTP traffic
and the ``requests`` dependency. The fake records what the public
function passed it (endpoint, headers, body) and returns a controllable
response (or ``None`` to simulate failure).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from lazycomics import llm_refiner  # noqa: E402
from lazycomics.llm_refiner import (  # noqa: E402
    DEFAULT_SYSTEM_PROMPT,
    refine_prompts_with_llm,
)
from lazycomics.project import create_project  # noqa: E402


# ---------------------------------------------------------------------------
# Test environment: tmp project, fake cwd (so config is found), patched LLM.
# ---------------------------------------------------------------------------


class _Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        # Patch Path.cwd so load_config() finds our config in this tmp dir.
        self._orig_cwd = Path.cwd
        Path.cwd = staticmethod(lambda: self.root)

        # Default config: minimal valid llm settings.
        self.write_config(
            "llm:\n  url: http://fake-llm/v1\n  model: fake-model\n"
        )

        # Project with a minimal CBML so create_project succeeds.
        cbml = self.root / "story.cbml"
        cbml.write_text(
            "## Test\naspect: 2:3\n\nPAGE preset:splash\n\nPANEL A\nloc: room\n> A test.\n"
        )
        self.project = create_project("p1", cbml, base_dir=self.root)

        # Intercept _call_llm. Each test can set self.response (str | None)
        # before triggering refinement; calls accumulate in self.calls.
        self.calls: list[dict] = []
        self.response: str | None = "REFINED OUTPUT"

        self._orig_call_llm = llm_refiner._call_llm

        def fake_call_llm(endpoint, headers, body):
            self.calls.append({
                "endpoint": endpoint,
                "headers": dict(headers),
                "body": json.loads(json.dumps(body)),  # deep copy
            })
            return self.response

        llm_refiner._call_llm = fake_call_llm

    def write_config(self, body: str):
        (self.root / "lazycomics_config.yaml").write_text(body)

    def write_prompt(self, panel_id: str, text: str):
        (self.project.prompts_dir / f"{panel_id}.txt").write_text(text)

    def write_neg_prompt(self, panel_id: str, text: str):
        (self.project.prompts_dir / f"{panel_id}.neg.txt").write_text(text)

    def write_enriched(self, panel_id: str, **fields):
        data = {
            "panel_id": panel_id,
            "page_index": 0,
            "panel_index": 0,
            "aspect_ratio": 2 / 3,
            "action": "",
            "shot_hint": "",
            "mood": None,
            "loc_identifier": None,
            "loc_description": "",
            "characters": [],
        }
        data.update(fields)
        (self.project.enriched_dir / f"{panel_id}.json").write_text(json.dumps(data))

    def close(self):
        Path.cwd = self._orig_cwd
        llm_refiner._call_llm = self._orig_call_llm
        self.tmp.cleanup()


def _with_env(fn):
    def wrapper():
        env = _Env()
        try:
            fn(env)
        finally:
            env.close()
    wrapper.__name__ = fn.__name__
    return wrapper


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@_with_env
def test_refines_single_prompt_and_writes_txt(env):
    env.write_prompt("p1", "original prompt")
    env.response = "refined prompt"
    refine_prompts_with_llm(env.project)

    txt = (env.project.prompts_dir / "p1.txt").read_text().rstrip()
    assert txt == "refined prompt"


@_with_env
def test_returns_panel_id_to_original_refined(env):
    env.write_prompt("p1", "original")
    env.response = "shiny new prompt"
    result = refine_prompts_with_llm(env.project)
    assert result == {"p1": ("original", "shiny new prompt")}


@_with_env
def test_writes_bak_with_original(env):
    env.write_prompt("p1", "original")
    env.response = "refined"
    refine_prompts_with_llm(env.project)
    bak = (env.project.prompts_dir / "p1.txt.bak").read_text().rstrip()
    assert bak == "original"


@_with_env
def test_existing_bak_is_not_overwritten(env):
    env.write_prompt("p1", "round-one-original")
    env.response = "round-one-refined"
    refine_prompts_with_llm(env.project)

    # Second pass: the .txt is now "round-one-refined".
    env.response = "round-two-refined"
    refine_prompts_with_llm(env.project)

    bak = (env.project.prompts_dir / "p1.txt.bak").read_text().rstrip()
    # .bak still holds the TRUE original from the first pass.
    assert bak == "round-one-original"


@_with_env
def test_backup_false_skips_bak(env):
    env.write_prompt("p1", "original")
    env.response = "refined"
    refine_prompts_with_llm(env.project, backup=False)
    assert not (env.project.prompts_dir / "p1.txt.bak").is_file()


@_with_env
def test_refines_multiple_panels(env):
    env.write_prompt("page_1_panel_1", "first")
    env.write_prompt("page_1_panel_2", "second")
    # Same response for both; check the count and that .txt files updated.
    env.response = "REFINED"
    result = refine_prompts_with_llm(env.project)
    assert set(result) == {"page_1_panel_1", "page_1_panel_2"}
    assert len(env.calls) == 2


@_with_env
def test_panels_filter_limits_to_specified(env):
    env.write_prompt("page_1_panel_1", "first")
    env.write_prompt("page_1_panel_2", "second")
    env.response = "REFINED"
    result = refine_prompts_with_llm(env.project, panels=["page_1_panel_2"])
    assert set(result) == {"page_1_panel_2"}
    assert len(env.calls) == 1


@_with_env
def test_neg_files_are_skipped(env):
    env.write_prompt("p1", "positive")
    env.write_neg_prompt("p1", "negatives go here")
    env.response = "REFINED"
    refine_prompts_with_llm(env.project)
    # The .neg.txt is untouched and was never sent to the LLM.
    assert (env.project.prompts_dir / "p1.neg.txt").read_text() == "negatives go here"
    assert len(env.calls) == 1


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


@_with_env
def test_llm_failure_preserves_original(env):
    env.write_prompt("p1", "do not lose me")
    env.response = None  # simulate failure
    result = refine_prompts_with_llm(env.project)
    assert result == {}  # no successful refinements
    # Original .txt untouched.
    assert (env.project.prompts_dir / "p1.txt").read_text() == "do not lose me"
    # No .bak written either — refinement never succeeded.
    assert not (env.project.prompts_dir / "p1.txt.bak").is_file()


@_with_env
def test_empty_llm_response_preserves_original(env):
    env.write_prompt("p1", "stay here")
    env.response = "   "  # whitespace only
    result = refine_prompts_with_llm(env.project)
    assert result == {}
    assert (env.project.prompts_dir / "p1.txt").read_text() == "stay here"


@_with_env
def test_empty_prompt_file_skipped(env):
    env.write_prompt("p1", "")
    refine_prompts_with_llm(env.project)
    assert env.calls == []  # never called the LLM


@_with_env
def test_missing_prompt_file_skipped(env):
    refine_prompts_with_llm(env.project, panels=["nonexistent_panel"])
    assert env.calls == []


# ---------------------------------------------------------------------------
# Config + system prompt precedence
# ---------------------------------------------------------------------------


@_with_env
def test_default_system_prompt_used_when_no_arg_no_config(env):
    env.write_prompt("p1", "x")
    refine_prompts_with_llm(env.project)
    sent_system = env.calls[0]["body"]["messages"][0]["content"]
    assert sent_system == DEFAULT_SYSTEM_PROMPT


@_with_env
def test_config_system_prompt_used_when_no_arg(env):
    env.write_config(
        "llm:\n"
        "  url: http://fake-llm/v1\n"
        "  model: fake-model\n"
        "  system_prompt: \"Project-specific system prompt\"\n"
    )
    env.write_prompt("p1", "x")
    refine_prompts_with_llm(env.project)
    sent_system = env.calls[0]["body"]["messages"][0]["content"]
    assert sent_system == "Project-specific system prompt"


@_with_env
def test_function_arg_overrides_config_system_prompt(env):
    env.write_config(
        "llm:\n"
        "  url: http://fake-llm/v1\n"
        "  model: fake-model\n"
        "  system_prompt: \"From config\"\n"
    )
    env.write_prompt("p1", "x")
    refine_prompts_with_llm(env.project, system_prompt="From arg")
    sent_system = env.calls[0]["body"]["messages"][0]["content"]
    assert sent_system == "From arg"


@_with_env
def test_missing_url_raises_clearly(env):
    env.write_config("llm:\n  model: fake-model\n")
    env.write_prompt("p1", "x")
    try:
        refine_prompts_with_llm(env.project)
    except ValueError as e:
        assert "llm.url" in str(e)
    else:
        raise AssertionError("Expected ValueError")


@_with_env
def test_missing_model_raises_clearly(env):
    env.write_config("llm:\n  url: http://fake-llm/v1\n")
    env.write_prompt("p1", "x")
    try:
        refine_prompts_with_llm(env.project)
    except ValueError as e:
        assert "llm.model" in str(e)
    else:
        raise AssertionError("Expected ValueError")


# ---------------------------------------------------------------------------
# HTTP details — endpoint, headers, body shape
# ---------------------------------------------------------------------------


@_with_env
def test_endpoint_is_chat_completions(env):
    env.write_prompt("p1", "x")
    refine_prompts_with_llm(env.project)
    assert env.calls[0]["endpoint"] == "http://fake-llm/v1/chat/completions"


@_with_env
def test_endpoint_strips_trailing_slash(env):
    env.write_config(
        "llm:\n  url: http://fake-llm/v1/\n  model: fake-model\n"
    )
    env.write_prompt("p1", "x")
    refine_prompts_with_llm(env.project)
    assert env.calls[0]["endpoint"] == "http://fake-llm/v1/chat/completions"


@_with_env
def test_api_key_added_to_authorization_header(env):
    env.write_config(
        "llm:\n"
        "  url: http://fake-llm/v1\n"
        "  model: fake-model\n"
        "  api_key: sk-test-12345\n"
    )
    env.write_prompt("p1", "x")
    refine_prompts_with_llm(env.project)
    assert env.calls[0]["headers"]["Authorization"] == "Bearer sk-test-12345"


@_with_env
def test_no_api_key_no_authorization_header(env):
    env.write_prompt("p1", "x")
    refine_prompts_with_llm(env.project)
    assert "Authorization" not in env.calls[0]["headers"]


@_with_env
def test_body_carries_model_and_messages(env):
    env.write_prompt("p1", "the prompt")
    refine_prompts_with_llm(env.project)
    body = env.calls[0]["body"]
    assert body["model"] == "fake-model"
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    assert "the prompt" in body["messages"][1]["content"]


# ---------------------------------------------------------------------------
# Context from enriched JSON
# ---------------------------------------------------------------------------


@_with_env
def test_user_message_includes_enriched_context(env):
    env.write_prompt("p1", "the prompt")
    env.write_enriched(
        "p1",
        action="Nova sprints",
        shot_hint="wide low angle",
        mood="urgent",
        loc_description="rain-slicked alley",
        characters=[
            {"identifier": "NOVA", "visual_description": "blue hair",
             "reference_images": [], "lora": None},
        ],
    )
    refine_prompts_with_llm(env.project)
    user_msg = env.calls[0]["body"]["messages"][1]["content"]
    assert "Panel context:" in user_msg
    assert "Action: Nova sprints" in user_msg
    assert "Shot: wide low angle" in user_msg
    assert "Mood: urgent" in user_msg
    assert "Setting: rain-slicked alley" in user_msg
    assert "NOVA (blue hair)" in user_msg
    # And the prompt itself still appears.
    assert "Prompt to refine:" in user_msg
    assert "the prompt" in user_msg


@_with_env
def test_user_message_omits_context_when_no_enriched_json(env):
    env.write_prompt("p1", "lonely prompt")
    refine_prompts_with_llm(env.project)
    user_msg = env.calls[0]["body"]["messages"][1]["content"]
    assert "Panel context:" not in user_msg
    assert "lonely prompt" in user_msg
