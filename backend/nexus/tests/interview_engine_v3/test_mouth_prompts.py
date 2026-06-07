import pytest


def test_all_mouth_prompt_files_load():
    from app.ai.prompts import PromptLoader
    loader = PromptLoader(version="v4")
    for name in ["engine/mouth/_persona", "engine/mouth/ask", "engine/mouth/probe", "engine/mouth/clarify",
                 "engine/mouth/redirect", "engine/mouth/reassure", "engine/mouth/answer_meta",
                 "engine/mouth/repeat", "engine/mouth/close", "engine/mouth/bridge"]:
        text = loader.get(name)
        assert text.strip(), f"{name} is empty"


def test_persona_has_placeholders_but_no_rubric_leak():
    from app.ai.prompts import PromptLoader
    persona = PromptLoader(version="v4").get("engine/mouth/_persona")
    # persona is session-personalized
    assert "{persona_name}" in persona
    # no-leak: the persona explicitly holds NO scoring/evaluation criteria
    low = persona.lower()
    assert "no scoring" in low or "no evaluation" in low or "hold no" in low


def test_bridge_is_gist_mirror_commit_to_nothing():
    from app.ai.prompts import PromptLoader
    bridge = PromptLoader(version="v4").get("engine/mouth/bridge")
    low = bridge.lower()
    assert "mirror" in low          # gist-mirror
    # commits to NOTHING about quality/next move
    assert "commit to nothing" in low or "commits to nothing" in low or "never evaluate" in low


def test_mouth_prompt_version_default_is_v4():
    from app.config import settings
    assert settings.engine_mouth_prompt_version == "v4"
