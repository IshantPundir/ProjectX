"""Curated opener vocabulary for the interview Speaker pipeline.

The orchestrator picks an opener from this library before each Speaker
LLM call, plays it as pre-cached audio, and tells the Speaker which
opener was spoken so the LLM can compose natural continuation content.

See docs/superpowers/specs/2026-05-10-opener-prefetch-architecture-design.md
"""
from app.modules.interview_engine.openers.library import (
    OpenerSelection,
    OpenerVariant,
    SubContext,
)

__all__ = [
    "OpenerSelection",
    "OpenerVariant",
    "SubContext",
]
