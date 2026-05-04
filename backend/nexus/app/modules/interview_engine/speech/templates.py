"""Engine-scoped TemplateLoader binding.

Constructs a process-singleton TemplateLoader pointed at
``app/modules/interview_engine/prompts/`` so structured-agent code
can call ``template_loader.get(role, name, version)`` without
re-binding the base path everywhere. The TemplateLoader class itself
lives in ``app.ai.prompts`` (per-template versioning, mtime-based
dev reload, sha256 hash helper, FileNotFoundError on miss).

Dev-mode reload is gated on ``settings.environment == "development"``
so production processes are cache-forever (process restart on deploy
invalidates).
"""
from __future__ import annotations

from pathlib import Path

from app.ai.prompts import TemplateLoader
from app.config import settings

# Repository layout:
# backend/nexus/app/modules/interview_engine/speech/templates.py
# parents[1] == backend/nexus/app/modules/interview_engine
ENGINE_PROMPTS_DIR: Path = Path(__file__).resolve().parents[1] / "prompts"

template_loader: TemplateLoader = TemplateLoader(
    base_path=ENGINE_PROMPTS_DIR,
    reload_on_change=(settings.environment == "development"),
)
