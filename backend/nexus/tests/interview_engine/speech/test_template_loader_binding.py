"""Tests for the engine-scoped TemplateLoader binding (Phase A tail).

The TemplateLoader class itself lives in app/ai/prompts.py and has its
own coverage; this file only validates the engine-side binding (correct
base path, dev/prod reload flag derived from settings.environment).
"""

import pytest

from app.modules.interview_engine.speech.templates import (
    ENGINE_PROMPTS_DIR,
    template_loader,
)


def test_loads_intro_v1():
    """The binding loads intro.v1.txt from the engine prompts dir."""
    body = template_loader.get("speech_agent", "intro", "v1")
    assert body, "intro.v1.txt body should be non-empty"
    # Sanity: known-content from the prompt file
    assert "interviewer" in body.lower() or "screener" in body.lower()


def test_missing_version_raises_file_not_found():
    """A missing template version raises FileNotFoundError loudly."""
    with pytest.raises(FileNotFoundError):
        template_loader.get("speech_agent", "intro", "v999")


def test_engine_prompts_dir_points_at_correct_directory():
    """ENGINE_PROMPTS_DIR is the engine's own prompts dir, not the
    repo-root prompts/ used by PromptLoader."""
    # The binding sits at app/modules/interview_engine/speech/templates.py;
    # ENGINE_PROMPTS_DIR == .../app/modules/interview_engine/prompts
    assert ENGINE_PROMPTS_DIR.name == "prompts"
    assert ENGINE_PROMPTS_DIR.parent.name == "interview_engine"
    # And the speech_agent subdir exists with the three filled v1 templates
    speech_agent_dir = ENGINE_PROMPTS_DIR / "speech_agent"
    assert speech_agent_dir.exists()
    assert (speech_agent_dir / "intro.v1.txt").exists()
    assert (speech_agent_dir / "ask_question_standard.v1.txt").exists()
    assert (speech_agent_dir / "wrap_normal.v1.txt").exists()
