"""Tests for report_scorer AIConfig knobs (Task 1).

Verifies that Settings fields and AIConfig properties for the offline
report scorer are wired correctly and that the effort-gating contract
(empty string = do not send reasoning_effort) is honoured.

Construction pattern mirrors test_engine_settings.py:
  - monkeypatch.setenv + Settings() for Settings field tests
  - AIConfig() (no args) for AIConfig property tests
"""

from app.ai.config import AIConfig
from app.config import Settings

# ---------------------------------------------------------------------------
# Settings field defaults — assert coded defaults are what we expect
# ---------------------------------------------------------------------------


def test_settings_report_scorer_model_default():
    """openai_report_scorer_model defaults to gpt-5.4 (full reasoning model)."""
    fields = Settings.model_fields
    assert fields["openai_report_scorer_model"].default == "gpt-5.4"


def test_settings_report_scorer_effort_default():
    """openai_report_scorer_effort defaults to 'medium'."""
    fields = Settings.model_fields
    assert fields["openai_report_scorer_effort"].default == "medium"


def test_settings_report_scorer_verbosity_default():
    """openai_report_scorer_verbosity defaults to 'low'."""
    fields = Settings.model_fields
    assert fields["openai_report_scorer_verbosity"].default == "low"


def test_settings_report_scorer_n_samples_default():
    """openai_report_scorer_n_samples defaults to 3."""
    fields = Settings.model_fields
    assert fields["openai_report_scorer_n_samples"].default == 3


def test_settings_report_scorer_prompt_version_default():
    """report_scorer_prompt_version defaults to 'v3' (current active engine prompt dir)."""
    fields = Settings.model_fields
    assert fields["report_scorer_prompt_version"].default == "v3"


def test_settings_report_scorer_prompt_cache_key_prefix_default():
    """report_scorer_prompt_cache_key_prefix defaults to 'judge'."""
    fields = Settings.model_fields
    assert fields["report_scorer_prompt_cache_key_prefix"].default == "judge"


# ---------------------------------------------------------------------------
# Settings fields — env-override path
# ---------------------------------------------------------------------------


def test_settings_report_scorer_fields_read_from_env(monkeypatch):
    """All report scorer Settings fields are overridable via environment variables."""
    monkeypatch.setenv("OPENAI_REPORT_SCORER_MODEL", "gpt-5.2")
    monkeypatch.setenv("OPENAI_REPORT_SCORER_EFFORT", "high")
    monkeypatch.setenv("OPENAI_REPORT_SCORER_VERBOSITY", "medium")
    monkeypatch.setenv("OPENAI_REPORT_SCORER_N_SAMPLES", "5")
    monkeypatch.setenv("REPORT_SCORER_PROMPT_VERSION", "v4")
    monkeypatch.setenv("REPORT_SCORER_PROMPT_CACHE_KEY_PREFIX", "scorer")

    s = Settings()
    assert s.openai_report_scorer_model == "gpt-5.2"
    assert s.openai_report_scorer_effort == "high"
    assert s.openai_report_scorer_verbosity == "medium"
    assert s.openai_report_scorer_n_samples == 5
    assert s.report_scorer_prompt_version == "v4"
    assert s.report_scorer_prompt_cache_key_prefix == "scorer"


# ---------------------------------------------------------------------------
# AIConfig property tests
# ---------------------------------------------------------------------------


def test_aiconfig_report_scorer_model(monkeypatch):
    """AIConfig.report_scorer_model surfaces the Settings field."""
    monkeypatch.setenv("OPENAI_REPORT_SCORER_MODEL", "gpt-5.1-turbo")
    cfg = AIConfig()
    assert cfg.report_scorer_model == "gpt-5.1-turbo"


def test_aiconfig_report_scorer_effort(monkeypatch):
    """AIConfig.report_scorer_effort surfaces the Settings field."""
    monkeypatch.setenv("OPENAI_REPORT_SCORER_EFFORT", "low")
    cfg = AIConfig()
    assert cfg.report_scorer_effort == "low"


def test_aiconfig_report_scorer_verbosity(monkeypatch):
    """AIConfig.report_scorer_verbosity surfaces the Settings field."""
    monkeypatch.setenv("OPENAI_REPORT_SCORER_VERBOSITY", "high")
    cfg = AIConfig()
    assert cfg.report_scorer_verbosity == "high"


def test_aiconfig_report_scorer_n_samples(monkeypatch):
    """AIConfig.report_scorer_n_samples surfaces the Settings field."""
    monkeypatch.setenv("OPENAI_REPORT_SCORER_N_SAMPLES", "7")
    cfg = AIConfig()
    assert cfg.report_scorer_n_samples == 7


def test_aiconfig_report_scorer_prompt_version(monkeypatch):
    """AIConfig.report_scorer_prompt_version surfaces the Settings field."""
    monkeypatch.setenv("REPORT_SCORER_PROMPT_VERSION", "v3")
    cfg = AIConfig()
    assert cfg.report_scorer_prompt_version == "v3"


def test_aiconfig_report_scorer_prompt_cache_key_prefix(monkeypatch):
    """AIConfig.report_scorer_prompt_cache_key_prefix surfaces the Settings field."""
    monkeypatch.setenv("REPORT_SCORER_PROMPT_CACHE_KEY_PREFIX", "scorer-v2")
    cfg = AIConfig()
    assert cfg.report_scorer_prompt_cache_key_prefix == "scorer-v2"


# ---------------------------------------------------------------------------
# Effort-gating contract: effort="" must be gateable (returns "")
# ---------------------------------------------------------------------------


def test_aiconfig_report_scorer_effort_empty_string_when_unset(monkeypatch):
    """When OPENAI_REPORT_SCORER_EFFORT is set to empty string, the property
    returns '' so callers can gate on `if ai_config.report_scorer_effort:`
    before forwarding reasoning_effort to the OpenAI client.

    This is the load-bearing effort-gating contract documented in app/ai/config.py.
    """
    monkeypatch.setenv("OPENAI_REPORT_SCORER_EFFORT", "")
    cfg = AIConfig()
    # Must return exactly "" — the caller gates on truthiness
    assert cfg.report_scorer_effort == ""
    # Confirm the gating pattern works as intended
    assert not cfg.report_scorer_effort


def test_aiconfig_report_scorer_effort_non_empty_is_truthy(monkeypatch):
    """A non-empty effort value is truthy — the caller will forward it to the API."""
    monkeypatch.setenv("OPENAI_REPORT_SCORER_EFFORT", "medium")
    cfg = AIConfig()
    assert cfg.report_scorer_effort
    assert cfg.report_scorer_effort == "medium"
