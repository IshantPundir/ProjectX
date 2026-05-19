"""Per-session STT plugin factory — keyterm assembly seam.

The factory function returns BOTH the STT plugin and the KeytermExtraction
so the caller (agent.py) can emit a single ``audio.stt.keyterms_applied``
audit event without re-running the assembler.

Sarvam ignores the keyterms argument (no equivalent feature). The actual
provider dispatch lives in app/ai/realtime.py.

Spec: docs/superpowers/specs/2026-05-19-deepgram-keyterm-migration-design.md
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.realtime import build_stt_plugin
from app.modules.interview_engine.keyterms import KeytermExtraction, assemble_keyterms
from app.modules.interview_runtime.schemas import SessionConfig

if TYPE_CHECKING:
    # Mirror the lazy-import discipline in app/ai/realtime.py — the LiveKit
    # plugin packages must NOT be loaded at module import time.
    from livekit.agents.stt import STT as _BaseSTT


def build_stt_plugin_for_session(
    *, session_config: SessionConfig,
) -> tuple["_BaseSTT", KeytermExtraction]:
    """Build the STT plugin for one session AND return the keyterm extraction.

    The caller (agent.py) is expected to emit the
    ``audio.stt.keyterms_applied`` audit event using the returned
    ``KeytermExtraction`` BEFORE constructing AgentSession.
    """
    extraction = assemble_keyterms(session_config)
    return build_stt_plugin(keyterms=extraction.terms), extraction
