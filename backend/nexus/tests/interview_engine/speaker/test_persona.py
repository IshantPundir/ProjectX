"""Tests for PersonaSpec dataclass + name resolution."""
from app.modules.interview_engine.speaker.persona import (
    DEFAULT_PERSONA, PersonaSpec, resolve_persona_name,
)


def test_default_persona_is_arjun() -> None:
    """Persona name is 'Arjun'. Note: as of the 2026-05-19 restructure
    the persona no longer carries an `archetype` (a job title for the
    agent) — claiming a title we can't verify was retired. The persona
    is identified by name + behavior bullets only.
    """
    assert DEFAULT_PERSONA.name == "Arjun"
    # The persona MUST NOT carry an archetype field that names a title.
    assert not hasattr(DEFAULT_PERSONA, "archetype"), (
        "Archetype field was intentionally removed (no claimed title)."
    )


def test_default_persona_is_frozen() -> None:
    import dataclasses
    assert dataclasses.is_dataclass(PersonaSpec)
    # frozen=True means attempting to set raises FrozenInstanceError
    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_PERSONA.name = "Someone Else"  # type: ignore[misc]


def test_behavior_bullets_present_and_concrete() -> None:
    """The new persona shape: behavior bullets, not an opener rotation.

    Replaces the retired `opener_rotation` tuple. Bullets describe
    OBSERVABLE behaviors (research-backed — adjectives produce vague
    output, behavior tics produce reproducible characterization).
    """
    assert isinstance(DEFAULT_PERSONA.behavior_bullets, tuple)
    assert len(DEFAULT_PERSONA.behavior_bullets) >= 6
    # Spot-check that at least one bullet names a concrete tic
    text = "\n".join(DEFAULT_PERSONA.behavior_bullets).lower()
    assert "mm" in text or "right" in text, (
        "behavior_bullets should name at least one concrete filler"
    )


def test_no_opener_rotation_field() -> None:
    """The hand-curated opener rotation was retired on 2026-05-19.

    Anti-regression: if anyone re-adds an `opener_rotation` field,
    they bring back the robotic-repetition bug.
    """
    assert not hasattr(DEFAULT_PERSONA, "opener_rotation"), (
        "opener_rotation was intentionally retired — use the "
        "Variety RULE in _preamble.txt + recent_reply_starts instead."
    )


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


def test_render_preamble_substitutes_name_and_register() -> None:
    from app.modules.interview_engine.speaker.persona import render_preamble
    template = "You are {name}. Register: {register}."
    out = render_preamble(template, DEFAULT_PERSONA)
    assert "Arjun" in out
    assert "Pronounced Indian English" in out


def test_render_preamble_emits_behavior_bullets_and_banned_bullets() -> None:
    from app.modules.interview_engine.speaker.persona import render_preamble
    template = (
        "How you talk:\n{behavior_bullets_bulleted}\n"
        "Banned:\n{vocab_banned_bulleted}"
    )
    out = render_preamble(template, DEFAULT_PERSONA)
    # Bulleted form: each line starts with "  - "
    assert "  - delve" in out
    # behavior_bullets entries are rendered with the "  - " prefix
    assert "  - " in out
    # The "{name}" placeholder inside behavior_bullets entries is
    # substituted with the actual persona name before bulleting.
    if any("{name}" in b for b in DEFAULT_PERSONA.behavior_bullets):
        assert "{name}" not in out


def test_render_preamble_deterministic() -> None:
    """Same input → byte-identical output. Critical for prompt caching."""
    from app.modules.interview_engine.speaker.persona import render_preamble
    template = "{name} {register} {behavior_bullets_bulleted}"
    first = render_preamble(template, DEFAULT_PERSONA)
    second = render_preamble(template, DEFAULT_PERSONA)
    assert first == second
