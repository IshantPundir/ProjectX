import json
import pathlib
import pytest

from app.modules.interview_runtime import project_signal_metadata
from app.modules.interview_runtime.evidence import SessionEvidence
from app.modules.reporting.service import build_report

pytestmark = pytest.mark.prompt_quality  # real OpenAI calls; excluded from default suite

_FX = pathlib.Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((_FX / name).read_text())


@pytest.mark.asyncio
async def test_real_emm_session_stays_borderline_with_honest_ios_basis():
    evidence = SessionEvidence.model_validate(_load("session_f2fd4b03_evidence.json"))
    questions = _load("session_f2fd4b03_questions.json")
    raw_signals = _load("session_f2fd4b03_snapshot_signals.json")
    signal_metadata = [m.model_dump() for m in project_signal_metadata(raw_signals)]

    report = await build_report(
        evidence=evidence, questions=questions, signal_metadata=signal_metadata,
        correlation_id="cid-regression", bank_id="bank-f2fd", signal_snapshot_id="snap-f2fd")

    assert report.verdict == "borderline"
    assert report.scoring_manifest.bank_id == "bank-f2fd"
    assert report.scoring_manifest.scorer_code_version == "qa-1"

    ios = next(s for s in report.signal_assessments if s.signal.startswith("iOS device management"))
    assert "thin" in ios.level_basis.lower()        # the dedicated iOS Wi-Fi answer was thin
    assert ios.cross_credit_applied is True          # lifted by the change-mgmt story

    ios_q = next(q for q in report.questions
                 if "Wi" in q.question_text and "iOS" in q.question_text)
    # The iOS Wi-Fi answer was thin, so a red flag SHOULD fire — but whether the
    # grader phrases/emits it is probabilistic LLM behaviour on this full real
    # session, so we lock only that the field is wired through here. The
    # deterministic content expectation (thin answer trips the red flag) is
    # covered by the controlled-input eval
    # tests/reporting/prompt_evals/test_question_grade_evals.py::
    # test_thin_dedicated_answer_grades_thin_and_trips_red_flag.
    assert isinstance(ios_q.red_flags_tripped, list)
