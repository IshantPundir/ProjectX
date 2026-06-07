def test_brain_system_prompt_loads_and_is_job_agnostic():
    from app.ai.prompts import PromptLoader

    text = PromptLoader(version="v4").load("engine/brain.system")
    assert text.strip()                       # non-empty
    # Job-agnostic: no template placeholders / interpolation left in the rendered prompt.
    assert "${" not in text and "{{" not in text
    assert "{job_title}" not in text and "{persona_name}" not in text
    # Spine concepts are present (collector-not-judge, thin≠bluff→elicit, DATA boundary).
    low = text.lower()
    assert "signal collector" in low
    assert "data" in low                      # the input-trust / DATA boundary section
    assert "elicit" in low                    # thin-is-not-a-bluff → elicit

def test_brain_prompt_version_default_is_v4():
    from app.config import settings
    assert settings.engine_brain_prompt_version == "v4"
