from app.modules.interview_engine.speaker.persona import (
    DEFAULT_PERSONA, resolve_persona_name,
)


class _FakeSettings:
    def __init__(self, agent_name=None):
        self.engine_agent_name = agent_name


class _FakeTenant:
    def __init__(self, agent_name=None):
        self.engine_agent_name = agent_name


def test_default_persona_acknowledgment_vs_evaluation():
    """Locked from Round 3.3 — must distinguish acknowledgment from evaluation."""
    text = "\n".join(DEFAULT_PERSONA["voice_traits"])
    assert "acknowledge" in text.lower()
    assert "evaluative" in text.lower()


def test_resolve_uses_tenant_first():
    name = resolve_persona_name(
        tenant_settings=_FakeTenant("Tenant Sam"),
        settings=_FakeSettings("Default Sam"),
    )
    assert name == "Tenant Sam"


def test_resolve_falls_back_to_settings():
    name = resolve_persona_name(
        tenant_settings=_FakeTenant(None),
        settings=_FakeSettings("Default Sam"),
    )
    assert name == "Default Sam"


def test_resolve_falls_back_to_default():
    name = resolve_persona_name(
        tenant_settings=_FakeTenant(None),
        settings=_FakeSettings(None),
    )
    assert name == "the interviewer"
