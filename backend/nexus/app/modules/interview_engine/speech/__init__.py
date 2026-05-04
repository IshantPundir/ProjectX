"""Speech Agent — generates every candidate-facing utterance via a
versioned template and a strict safety gate.

Phase A.5 ships the safety regex; Phase C wires the LLM-rendering
``SpeechAgent`` class itself. Both share this module's public API so
callers (``structured_agent.py``) import from one place.
"""
from app.modules.interview_engine.speech.safety import (
    SafetyResult,
    SafetyViolation,
    ViolationCategory,
    check_safety,
)

__all__ = [
    "SafetyResult",
    "SafetyViolation",
    "ViolationCategory",
    "check_safety",
]
