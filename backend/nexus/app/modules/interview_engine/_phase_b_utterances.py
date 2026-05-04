"""Throwaway hardcoded utterances for Phase B.

DELETE this file when Phase C ships the LLM-rendered Speech Agent. The
orchestrator's call sites (in structured_agent.py) get repointed to
speech.deliveries.render_<template> at that time. If you are reading this
in Phase C or later, this file should not exist.

Phase B uses these literal strings to exercise the orchestrator's flow
end-to-end before the Speech Agent class is built. The candidate
experience sounds robotic; that is intended. Phase C replaces every call
site with `await speech_agent.render(template, version, inputs)` →
`await session.say(rendered.text)`, with the same single-entry-point
discipline.

Four strings ship in Phase B:
- INTRO with placeholders: {name}, {role}, {minutes}
- ASK_QUESTION_STANDARD with: {question_text}
- WRAP_NORMAL (no placeholders)
- _PHASE_B_SAFETY_FALLBACK_TEXT — load-bearing recovery path used by
  StructuredInterviewAgent._say when an utterance fails check_safety.

All four are designed to pass `speech.safety.check_safety` cleanly. The
fallback's safety is enforced at module import time (see assertion
below) so a regression that would crash the agent in production fails
loudly during boot instead.
"""
from app.modules.interview_engine.speech.safety import check_safety

INTRO = (
    "Hi {name}, I'll be running a short technical screen for the {role} "
    "role today. We'll be about {minutes} minutes. Take your time, and "
    "feel free to ask me to repeat anything. Let's get started."
)

ASK_QUESTION_STANDARD = "Got it. Next question: {question_text}"

WRAP_NORMAL = (
    "That's everything from my side. The recruiting team will be in "
    "touch with next steps."
)

_PHASE_B_SAFETY_FALLBACK_TEXT = (
    "Let me ask you about something else. The recruiting team will "
    "follow up with you."
)

# Module-import-time fence: a safety regression in the fallback itself
# would otherwise allow the agent to limp on into production. Failing the
# import is the right loudness for a load-bearing recovery path.
#
# `if/raise` instead of `assert` — `python -O` / PYTHONOPTIMIZE strips
# `assert` statements, which would silently disable this load-bearing
# guarantee. The fence runs unconditionally regardless of optimization.
if not check_safety(_PHASE_B_SAFETY_FALLBACK_TEXT).is_safe:
    raise RuntimeError(
        "_PHASE_B_SAFETY_FALLBACK_TEXT failed check_safety — fix the "
        "string before the agent process is allowed to start."
    )
