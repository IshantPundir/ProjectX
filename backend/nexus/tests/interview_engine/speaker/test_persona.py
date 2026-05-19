"""Tests for PersonaSpec dataclass + name resolution."""
from app.modules.interview_engine.speaker.persona import (
    DEFAULT_PERSONA, PersonaSpec, resolve_persona_name,
)


def test_default_persona_is_arjun() -> None:
    assert DEFAULT_PERSONA.name == "Arjun"
    assert "Senior Engineering Manager" in DEFAULT_PERSONA.archetype


def test_default_persona_is_frozen() -> None:
    import dataclasses
    assert dataclasses.is_dataclass(PersonaSpec)
    # frozen=True means attempting to set raises FrozenInstanceError
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_PERSONA.name = "Someone Else"  # type: ignore[misc]


def test_opener_rotation_is_tuple_of_at_least_eight() -> None:
    assert isinstance(DEFAULT_PERSONA.opener_rotation, tuple)
    assert len(DEFAULT_PERSONA.opener_rotation) >= 8


def test_vocab_banned_contains_top_llm_tells() -> None:
    banned_lower = {v.lower() for v in DEFAULT_PERSONA.vocab_banned}
    # The literature-consensus top tells must be banned
    for required in ("delve", "leverage", "great question"):
        assert required in banned_lower, f"{required!r} must be banned"


def test_fallback_strings_in_arjun_voice() -> None:
    # Arjun-voice tells: 'Mm' opener, em-dash pause, 'Kindly'/'Right, so' patterns
    assert DEFAULT_PERSONA.fallback_recovery.startswith("Mm")
    assert "—" in DEFAULT_PERSONA.fallback_empty_output
    assert "{bank_text}" in DEFAULT_PERSONA.fallback_empty_output
    assert "{comma_name}" in DEFAULT_PERSONA.fallback_session_ended


def test_speaker_llm_temperature_is_07() -> None:
    assert DEFAULT_PERSONA.speaker_llm_temperature == 0.7


def test_resolve_persona_name_falls_back_to_arjun() -> None:
    class _Empty:
        engine_agent_name = None

    name = resolve_persona_name(tenant_settings=_Empty(), settings=_Empty())
    assert name == "Arjun"


def test_resolve_persona_name_ignores_tenant_override() -> None:
    """Persona name is locked to PersonaSpec.name; tenant override is ignored.

    Retired the override path on 2026-05-19 after a stale tenant value
    leaked into a live session. The persona identity (Arjun) is a
    product-wide constant, not a tenant configurable.
    """
    class _Tenant:
        engine_agent_name = "Priya"

    class _Settings:
        engine_agent_name = "Ignored"

    name = resolve_persona_name(tenant_settings=_Tenant(), settings=_Settings())
    assert name == "Arjun"


def test_resolve_persona_name_ignores_settings_default() -> None:
    """Same lock: env-level override is ignored too."""
    class _Tenant:
        engine_agent_name = None

    class _Settings:
        engine_agent_name = "Configured"

    name = resolve_persona_name(tenant_settings=_Tenant(), settings=_Settings())
    assert name == "Arjun"


def test_render_preamble_substitutes_name_and_archetype() -> None:
    from app.modules.interview_engine.speaker.persona import render_preamble
    template = "You are {name}, {archetype}. Register: {register}."
    out = render_preamble(template, DEFAULT_PERSONA)
    assert "Arjun" in out
    assert "Senior Engineering Manager" in out
    assert "Pronounced Indian English" in out


def test_render_preamble_emits_bulleted_lists() -> None:
    from app.modules.interview_engine.speaker.persona import render_preamble
    template = (
        "Openers:\n{opener_rotation_bulleted}\n"
        "Banned:\n{vocab_banned_bulleted}"
    )
    out = render_preamble(template, DEFAULT_PERSONA)
    # Bulleted form: each line starts with "  - "
    assert "  - See —" in out
    assert "  - delve" in out


def test_render_preamble_deterministic() -> None:
    """Same input → byte-identical output. Critical for prompt caching."""
    from app.modules.interview_engine.speaker.persona import render_preamble
    template = "{name} {archetype} {opener_rotation_bulleted}"
    first = render_preamble(template, DEFAULT_PERSONA)
    second = render_preamble(template, DEFAULT_PERSONA)
    assert first == second
