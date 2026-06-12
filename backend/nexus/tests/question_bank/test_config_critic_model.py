from app.ai.config import AIConfig


def test_critic_model_defaults_to_a_value():
    cfg = AIConfig()
    assert cfg.question_bank_critic_model  # non-empty default


def test_critic_effort_defaults_empty():
    # Effort-gating contract: default empty so a chat-model override is safe.
    cfg = AIConfig()
    assert cfg.question_bank_critic_effort == ""


def test_critic_model_env_override(monkeypatch):
    monkeypatch.setenv("OPENAI_QUESTION_BANK_CRITIC_MODEL", "gpt-5.4")
    cfg = AIConfig()
    assert cfg.question_bank_critic_model == "gpt-5.4"
