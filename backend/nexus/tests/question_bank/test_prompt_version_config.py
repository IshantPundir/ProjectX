"""Bank-gen prompts live in prompts/v3 (the follow-ups / governed-dimensions rewrite)."""

from app.ai.config import AIConfig


def test_default_bank_prompt_version_is_v3():
    assert AIConfig().question_bank_prompt_version == "v3"


def test_bank_prompt_version_env_override(monkeypatch):
    monkeypatch.setenv("QUESTION_BANK_PROMPT_VERSION", "v1")
    assert AIConfig().question_bank_prompt_version == "v1"
