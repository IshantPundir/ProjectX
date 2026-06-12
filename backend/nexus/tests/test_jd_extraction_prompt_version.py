from app.ai.config import AIConfig


def test_jd_signal_extraction_prompt_version_defaults_v2():
    assert AIConfig().jd_signal_extraction_prompt_version == "v2"


def test_jd_signal_extraction_prompt_version_env_override(monkeypatch):
    monkeypatch.setenv("JD_SIGNAL_EXTRACTION_PROMPT_VERSION", "v1")
    assert AIConfig().jd_signal_extraction_prompt_version == "v1"
