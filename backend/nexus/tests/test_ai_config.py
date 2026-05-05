"""Tests for app.ai.config — AIConfig env-driven model selection.

Phase C adds the Speech Agent (LLM-rendered utterances) keys. The agent
streams plain-text tokens via get_openai_raw_client() (NOT instructor-
wrapped) into session.say() → Cartesia TTS. Chat-tier model (NOT
reasoning) for low first-token latency in the candidate-perceived gap
between turns. Default-empty effort per the effort-gating contract
documented in app/ai/config.py module docstring.
"""

from __future__ import annotations


def test_speech_agent_model_reads_from_settings(monkeypatch):
    """Phase C: speech_agent_model is the Speech Agent's batch (non-realtime)
    OpenAI model. Defaults to gpt-5.3-chat-latest — chat-tier (NOT reasoning)
    so first-token latency stays low. Mirrors interview_llm_model for
    consistency with the realtime path's chat-tier choice."""
    from app.ai.config import ai_config
    assert ai_config.speech_agent_model == "gpt-5.3-chat-latest"


def test_speech_agent_effort_default_empty():
    """Default-empty effort follows the same effort-gating contract as
    evaluator_*_effort. Empty string means 'do not forward
    reasoning_effort to OpenAI' — chat models 400 on the param."""
    from app.ai.config import ai_config
    assert ai_config.speech_agent_effort == ""
