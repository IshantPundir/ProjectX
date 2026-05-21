"""0045: stage_questions gains primary_signal; question_kind CHECK switches to the new taxonomy."""

from app.modules.question_bank.models import StageQuestion


def test_primary_signal_column_present_and_nullable():
    col = StageQuestion.__table__.columns["primary_signal"]
    assert col.nullable is True


def test_question_kind_check_is_new_taxonomy():
    checks = {
        c.name: c.sqltext.text
        for c in StageQuestion.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
    }
    body = checks["stage_questions_question_kind_check"]
    for v in ("experience_check", "behavioral", "technical_scenario", "compliance_binary"):
        assert v in body
    for old in ("technical_depth", "behavioral_star", "open_culture"):
        assert old not in body
