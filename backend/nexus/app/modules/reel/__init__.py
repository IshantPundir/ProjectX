"""Candidate Reel module — AI-directed ~60s highlight reel from a session.

Public API is import-light (model / schemas / service only). The render path
(``render``, ``cards``, ``tts``, ``director``, ``actors``) pulls Pillow / livekit
TTS / ffmpeg and is imported directly by the vision worker + actor, never here,
so the lean nexus/API image stays free of those deps.
"""
from app.modules.reel.models import SessionReel
from app.modules.reel.schemas import ReelChapter, ReelPlayback, ReelStatus
from app.modules.reel.service import (
    build_playback,
    check_eligibility,
    eligibility_decision,
    get_reel,
    request_reel,
)

__all__ = [
    "SessionReel",
    "ReelChapter",
    "ReelPlayback",
    "ReelStatus",
    "build_playback",
    "check_eligibility",
    "eligibility_decision",
    "get_reel",
    "request_reel",
]
