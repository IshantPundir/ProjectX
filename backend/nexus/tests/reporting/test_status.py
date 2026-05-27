from app.modules.reporting.scoring.status import derive_status
from app.modules.reporting.scoring.types import ScoredUnit


def unit(qid="q", kind="technical_scenario", engaged=True, wc=20):
    return ScoredUnit(question_id=qid, question_text="t", candidate_answer="a",
                      answer_start_ms=0, probes_fired=0, clarifies=0, word_count=wc,
                      candidate_engaged=engaged, question_kind=kind)


def test_passed_factual_sufficient():
    b, _ = derive_status(unit(kind="experience_check"), signal_states={"S": "sufficient"},
                         signal_defs={"S": ("experience", False, "required")},
                         no_experience=False, closed_before_complete=False)
    assert b == "passed"


def test_failed_required_skill():
    b, _ = derive_status(unit(), signal_states={"S": "failed"},
                         signal_defs={"S": ("competency", False, "required")},
                         no_experience=False, closed_before_complete=False)
    assert b == "failed_required"


def test_not_demonstrated_on_no_experience():
    b, _ = derive_status(unit(engaged=False), signal_states={"S": "none"},
                         signal_defs={"S": ("competency", False, "required")},
                         no_experience=True, closed_before_complete=False)
    assert b == "not_demonstrated"


def test_not_attempted_when_unengaged_no_signal():
    b, _ = derive_status(unit(engaged=False, wc=1), signal_states={"S": "none"},
                         signal_defs={"S": ("competency", False, "required")},
                         no_experience=False, closed_before_complete=False)
    assert b == "not_attempted"


def test_not_fully_assessed_when_closed_early():
    b, _ = derive_status(unit(), signal_states={"S": "partial"},
                         signal_defs={"S": ("competency", False, "required")},
                         no_experience=False, closed_before_complete=True)
    assert b == "not_fully_assessed"


def test_partial_default():
    b, _ = derive_status(unit(), signal_states={"S": "partial"},
                         signal_defs={"S": ("competency", False, "required")},
                         no_experience=False, closed_before_complete=False)
    assert b == "partial"
