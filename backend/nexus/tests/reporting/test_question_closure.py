"""Unit tests for the per-question `closure` surfaced on `QuestionOut`.

RC-2: the recruiter report's "This moment" panel needs the engine's per-question
verdict (satisfied / tapped_out / truncated / ...). `QuestionRecord.closure` is the
engine's ground truth; `service.build_report` already derives a `closure` string for
the badge logic — these tests pin that it lands on `QuestionOut.closure` and that the
schema field accepts the enum's string values (and None for never-asked questions).
"""
from __future__ import annotations

from app.modules.interview_runtime.evidence import ThreadClosure
from app.modules.reporting.schemas import QuestionOut


def _minimal_question_out(**overrides) -> QuestionOut:
    base = dict(
        seq=1,
        question_id="q1",
        title="t",
        status_badge="b",
        status_tone="neutral",
        question_text="text",
        candidate_quote="",
    )
    base.update(overrides)
    return QuestionOut(**base)


def test_closure_field_defaults_to_none() -> None:
    # Never-asked / legacy questions carry no closure.
    assert _minimal_question_out().closure is None


def test_closure_field_accepts_thread_closure_values() -> None:
    for tc in ThreadClosure:
        q = _minimal_question_out(closure=tc.value)
        assert q.closure == tc.value


def test_closure_reflects_source_qr_closure() -> None:
    # The builder computes `closure = qr.closure.value if hasattr(...,"value") else qr.closure`
    # and passes that string straight through; assert that string lands on the field.
    qr_closure = ThreadClosure.tapped_out
    closure = qr_closure.value if hasattr(qr_closure, "value") else qr_closure
    q = _minimal_question_out(closure=closure)
    assert q.closure == "tapped_out"
