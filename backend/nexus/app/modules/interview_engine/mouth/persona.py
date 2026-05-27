"""Persona preamble rendering for the mouth (Conversation Plane).

The preamble is the BYTE-STABLE cache prefix (R6 / DESIGN-SPEC §11): it is rendered once
per session from the versioned `engine/mouth/_persona` prompt with the session's persona
name + role substituted, and is identical across every turn of that session. It carries
the identity lock (injection defense), anti-sycophancy, and voice discipline. It holds NO
rubric/evidence — the mouth is no-leak by construction. Pure: no livekit, no LLM.
"""

from __future__ import annotations

from app.ai.prompts import PromptLoader

_PERSONA_PROMPT = "engine/mouth/_persona"


def render_persona_preamble(*, loader: PromptLoader, persona_name: str, job_title: str) -> str:
    """Render the persona system preamble for a session (deterministic / byte-stable)."""
    template = loader.get(_PERSONA_PROMPT)
    return template.format(persona_name=persona_name, job_title=job_title)
