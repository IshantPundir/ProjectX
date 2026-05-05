"""Phase C speech package public API.

Phase A landed `templates.py` (template_loader binding) and `safety.py`
(retired in Phase C — see design doc §11.5 v3 for the prompt-only
safety model; safety.py is deleted in Task 14 of the Phase C plan).

Phase C exports:
    - SpeechAgent: the rendering service class
    - SpeechRenderHandle: Protocol both implementations satisfy
    - StreamingRenderHandle: live LLM path implementation
    - StaticFallbackHandle: fallback path implementation
    - SpeechRenderError: raised for template/placeholder errors and
      post-retry-exhaustion infrastructure errors
    - RenderMetadata: metadata returned by handle.metadata Future
"""
from app.modules.interview_engine.speech.agent import (
    RenderMetadata,
    SpeechAgent,
    SpeechRenderError,
    SpeechRenderHandle,
    StreamingRenderHandle,
)
from app.modules.interview_engine.speech.fallbacks import StaticFallbackHandle
from app.modules.interview_engine.speech.templates import (
    ENGINE_PROMPTS_DIR,
    template_loader,
)

__all__ = [
    "ENGINE_PROMPTS_DIR",
    "RenderMetadata",
    "SpeechAgent",
    "SpeechRenderError",
    "SpeechRenderHandle",
    "StaticFallbackHandle",
    "StreamingRenderHandle",
    "template_loader",
]
