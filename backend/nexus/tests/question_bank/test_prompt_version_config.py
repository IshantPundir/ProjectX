"""Bank-gen prompts live in prompts/v2 (the spoken-question rewrite)."""

from app.ai.config import AIConfig


def test_default_bank_prompt_version_is_v2():
    assert AIConfig().question_bank_prompt_version == "v2"


def test_bank_prompt_version_env_override(monkeypatch):
    monkeypatch.setenv("QUESTION_BANK_PROMPT_VERSION", "v1")
    assert AIConfig().question_bank_prompt_version == "v1"
