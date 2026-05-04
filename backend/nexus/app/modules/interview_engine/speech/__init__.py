"""Speech Agent — generates every candidate-facing utterance via a
versioned template and a strict safety gate.

Phase A.5 ships the safety regex; Phase A tail ships the template
loader binding; Phase C wires the LLM-rendering ``SpeechAgent`` class
itself. All three share this module's public API so callers
(``structured_agent.py``) import from one place.
"""
from app.modules.interview_engine.speech.safety import (
    SafetyResult,
    SafetyViolation,
    ViolationCategory,
    check_safety,
)
from app.modules.interview_engine.speech.templates import (
    ENGINE_PROMPTS_DIR,
    template_loader,
)

__all__ = [
    "ENGINE_PROMPTS_DIR",
    "SafetyResult",
    "SafetyViolation",
    "ViolationCategory",
    "check_safety",
    "template_loader",
]
