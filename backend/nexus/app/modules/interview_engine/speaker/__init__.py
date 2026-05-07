"""Speaker subpackage."""
from app.modules.interview_engine.speaker.input_builder import build_speaker_input
from app.modules.interview_engine.speaker.persona import (
    DEFAULT_PERSONA, resolve_persona_name,
)
from app.modules.interview_engine.speaker.service import (
    SpeakerService, SpeakerStreamHandle,
)


__all__ = [
    "SpeakerService", "SpeakerStreamHandle",
    "build_speaker_input",
    "DEFAULT_PERSONA", "resolve_persona_name",
]
