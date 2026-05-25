from app.modules.reporting.scoring.opportunity import classify
from app.modules.reporting.scoring.types import ScoredUnit

def _unit(**kw):
    base = dict(question_id="q", question_text="Q", candidate_answer="A",
                answer_start_ms=0, probes_fired=0, clarifies=0, word_count=0,
                candidate_engaged=True)
    base.update(kw); return ScoredUnit(**base)

def test_full_when_probed():
    assert classify(_unit(probes_fired=1, word_count=3, candidate_engaged=True)) == "full"

def test_full_when_substantive_answer():
    assert classify(_unit(probes_fired=0, word_count=20, candidate_engaged=True)) == "full"

def test_partial_when_barely_engaged_no_probe():
    assert classify(_unit(probes_fired=0, word_count=3, candidate_engaged=True)) == "partial"

def test_none_when_not_engaged_and_no_probe():
    assert classify(_unit(probes_fired=0, word_count=2, candidate_engaged=False)) == "none"

def test_full_for_on_target_idk_when_probed():
    assert classify(_unit(candidate_answer="I don't know", probes_fired=1,
                          word_count=3, candidate_engaged=True)) == "full"
