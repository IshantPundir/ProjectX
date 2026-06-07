"""
Mouth persona rendering — E2 task.

build_persona:   Loads the engine/mouth/_persona prompt and substitutes
                 {persona_name} and {job_title} using str.replace (NOT
                 str.format — the prompt may contain other brace tokens that
                 must be left untouched).

build_mouth_messages:
                 Assembles the three-part message list for the real-line LLM
                 call: [persona preamble | per-act block | dynamic suffix].
                 The dynamic suffix carries ONLY speakable / delivery data —
                 NO rubric fields ever reach this function (the Directive has
                 none), which is the structural no-leak guarantee.
"""
from __future__ import annotations

from app.modules.interview_engine.contracts import MouthTurnInput


def build_persona(
    *,
    persona_name: str,
    job_title: str,
    version: str | None = None,
) -> str:
    """Load and render the mouth persona system prompt.

    Uses str.replace (not str.format) so any other brace tokens in the prompt
    body (e.g. example placeholders, future tokens) are left intact.

    Parameters
    ----------
    persona_name:
        The interviewer's display name (e.g. "Arjun"). Substituted for
        ``{persona_name}`` in the prompt template.
    job_title:
        The job title for the role being screened (e.g. "Integration Engineer").
        Substituted for ``{job_title}`` in the prompt template.
    version:
        Prompt version directory (e.g. "v4"). Defaults to
        ``ai_config.engine_mouth_prompt_version`` when None.
    """
    # Lazy import keeps this module free of app startup overhead in test collection.
    from app.ai.prompts import PromptLoader

    if version is None:
        from app.ai.config import ai_config
        version = ai_config.engine_mouth_prompt_version

    raw = PromptLoader(version).get("engine/mouth/_persona")
    rendered = raw.replace("{persona_name}", persona_name).replace("{job_title}", job_title)
    return rendered


def build_mouth_messages(
    *,
    persona: str,
    act_block: str,
    mouth_input: MouthTurnInput,
) -> list[dict]:
    """Build the three-part message list for the mouth LLM call.

    Structure
    ---------
    [
        {"role": "system", "content": <persona preamble>},
        {"role": "system", "content": <per-act instruction block>},
        {"role": "user",   "content": <dynamic suffix>},
    ]

    The dynamic suffix carries ONLY:
      - SAY          — the directive's speakable text (or empty)
      - TONE         — delivery tone enum value
      - JUST SAID    — the bridge line already spoken this turn (the mouth
                       continues from it, never re-acknowledges)
      - RECENT OPENERS — connectives used recently (pick a different one)
      - SPOKEN SETUP — optional orienting clause

    NO rubric, NO brain reasoning, NO scoring criteria EVER appear here.
    This is the structural no-leak guarantee: the Directive type itself carries
    no rubric fields, so there is nothing to inject even accidentally.
    """
    directive = mouth_input.directive

    say_text = directive.say or ""
    tone_text = directive.tone.value
    just_said_text = mouth_input.just_said or ""
    recent_openers_text = ", ".join(mouth_input.recent_openers) if mouth_input.recent_openers else ""
    spoken_setup_text = directive.spoken_setup or ""

    dynamic_suffix = (
        f"SAY: {say_text}\n"
        f"TONE: {tone_text}\n"
        f"JUST SAID: {just_said_text} (continue from this, don't re-acknowledge)\n"
        f"RECENT OPENERS: {recent_openers_text} (pick a different one)\n"
        f"SPOKEN SETUP: {spoken_setup_text}"
    )

    return [
        {"role": "system", "content": persona},
        {"role": "system", "content": act_block},
        {"role": "user", "content": dynamic_suffix},
    ]
