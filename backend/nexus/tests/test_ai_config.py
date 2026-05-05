"""Tests for app.ai.config — AIConfig env-driven model selection.

Phase C adds the Speech Agent (LLM-rendered utterances) keys. The agent
streams plain-text tokens via get_openai_raw_client() (NOT instructor-
wrapped) into session.say() → Cartesia TTS. Mid-tier model + default-
empty effort per the effort-gating contract documented in
app/ai/config.py module docstring.
"""

from __future__ import annotations


def test_speech_agent_model_reads_from_settings(monkeypatch):
    """Phase C: speech_agent_model is the Speech Agent's batch (non-realtime)
    OpenAI model. Defaults to gpt-5-mini (mid-tier per design doc §5.6)."""
    from app.ai.config import ai_config
    assert ai_config.speech_agent_model == "gpt-5-mini"


def test_speech_agent_effort_default_empty():
    """Default-empty effort follows the same effort-gating contract as
    evaluator_*_effort. Empty string means 'do not forward
    reasoning_effort to OpenAI' — chat models 400 on the param."""
    from app.ai.config import ai_config
    assert ai_config.speech_agent_effort == ""
