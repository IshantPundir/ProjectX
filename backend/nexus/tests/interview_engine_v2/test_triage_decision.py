from app.modules.interview_engine_v2.triage.decision import TriageDecision, TriageKind, TriageRoute


def test_triage_decision_constructs_and_defaults():
    d = TriageDecision(reasoning="explicit thinking pause", kind=TriageKind.answering,
                       answer_complete=False, route=TriageRoute.handled,
                       spoken_line="Take your time…")
    assert d.route is TriageRoute.handled
    assert d.replay_last_question is False


def test_triage_decision_is_strict_schema_safe():
    # instructor TOOLS_STRICT rejects free-form dicts — assert no dict[...] fields exist
    import typing
    for name, field in TriageDecision.model_fields.items():
        origin = typing.get_origin(field.annotation)
        assert origin is not dict, f"{name} is a dict — strict-schema 400 risk (see c94f5b03)"
