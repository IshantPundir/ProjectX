"""A.1 — `build_session_config` projects `SessionConfig.signal_metadata`
from the latest confirmed `JobPostingSignalSnapshot.signals` JSONB.

Three round-trips covered:

1. Recruiter-edited snapshot (signals dicts include `evaluation_method`):
   the metadata is preserved verbatim and ordered to match `signals`.

2. Initial-extraction-style snapshot (signals dicts WITHOUT
   `evaluation_method`): `_project_signal_metadata` fills the field via
   `default_evaluation_method(type, stage)`, mirroring the recruiter-facing
   read path in `jd/router.py::_snapshot_to_response`.

3. Off-spec rows (non-dict entries in the JSONB array, e.g. legacy
   pre-v2 snapshots) are dropped with a warning rather than crashing
   session start. This is defense-in-depth — a confirmed snapshot
   should never carry such rows in production.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text as sql_text

from app.modules.candidates.models import Candidate, CandidateJobAssignment
from app.modules.interview_runtime import build_session_config
from app.modules.interview_runtime.errors import EmptySignalMetadataError
from app.modules.interview_runtime.service import _project_signal_metadata
from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot
from app.modules.pipelines.models import JobPipelineInstance, JobPipelineStage
from app.modules.question_bank.models import StageQuestion, StageQuestionBank
from app.modules.session.models import Session
from tests.conftest import (
    create_test_client,
    create_test_org_unit,
    create_test_user,
)

_VALID_PROFILE = {
    "about": "B2B SaaS serving Fortune 500 retail clients in the UK and EU.",
    "industry": "Technology",
    "hiring_bar": "Senior engineers who own outcomes end to end.",
}


def _signal_dict(
    *,
    value: str,
    type_: str,
    priority: str,
    weight: int,
    knockout: bool,
    stage: str,
    evaluation_method: str | None = "verbal_response",
    evaluation_hint: str | None = None,
) -> dict:
    return {
        "value": value,
        "type": type_,
        "priority": priority,
        "weight": weight,
        "knockout": knockout,
        "stage": stage,
        "evaluation_method": evaluation_method,
        "evaluation_hint": evaluation_hint,
        "source": "ai_extracted",
        "inference_basis": None,
    }


# ---------------------------------------------------------------------------
# Pure-function unit tests for _project_signal_metadata
# ---------------------------------------------------------------------------


def test_project_signal_metadata_preserves_recruiter_edits():
    """When the snapshot dict already carries evaluation_method (recruiter-
    edited path), it must be preserved verbatim."""
    raw = [
        _signal_dict(
            value="UK shift", type_="experience", priority="required",
            weight=3, knockout=True, stage="screen",
            evaluation_method="scenario_walkthrough",
            evaluation_hint="Confirm 1pm-9pm GMT availability.",
        ),
    ]
    out = _project_signal_metadata(raw)
    assert len(out) == 1
    sm = out[0]
    assert sm.value == "UK shift"
    assert sm.type == "experience"
    assert sm.priority == "required"
    assert sm.weight == 3
    assert sm.knockout is True
    assert sm.stage == "screen"
    assert sm.evaluation_method == "scenario_walkthrough"
    assert sm.evaluation_hint == "Confirm 1pm-9pm GMT availability."


def test_project_signal_metadata_defaults_evaluation_method_when_missing():
    """Initial-extraction snapshots persist SignalItemV2 dumps which lack
    `evaluation_method`. The projector must default via
    `default_evaluation_method(type, stage)` rather than failing validation."""
    raw = [
        # `evaluation_method` absent from dict (initial extraction)
        {
            "value": "Python",
            "type": "competency",
            "priority": "preferred",
            "weight": 2,
            "knockout": False,
            "stage": "interview",
            "source": "ai_extracted",
            "inference_basis": None,
        },
        # `evaluation_method` explicitly None (recruiter-input round-trip
        # before server defaulting — the field is nullable on input)
        {
            "value": "PCI-DSS",
            "type": "credential",
            "priority": "required",
            "weight": 3,
            "knockout": True,
            "stage": "screen",
            "evaluation_method": None,
            "source": "ai_extracted",
            "inference_basis": None,
        },
    ]
    out = _project_signal_metadata(raw)
    assert len(out) == 2
    # competency + interview → "code_exercise" per _EVALUATION_DEFAULTS
    assert out[0].evaluation_method == "code_exercise"
    # credential + screen → "credential_verify" per _EVALUATION_DEFAULTS
    assert out[1].evaluation_method == "credential_verify"


def test_project_signal_metadata_drops_non_dict_rows():
    """Off-spec rows are skipped (not crashed-on)."""
    raw = [
        "stale_legacy_string_signal",
        _signal_dict(
            value="Conflict resolution", type_="behavioral",
            priority="preferred", weight=2, knockout=False, stage="interview",
            evaluation_method="behavioral_question",
        ),
        None,
    ]
    out = _project_signal_metadata(raw)
    # Only the dict survives; the str and None are dropped.
    assert len(out) == 1
    assert out[0].value == "Conflict resolution"


def test_project_signal_metadata_preserves_order():
    """`signal_metadata[i]` aligns with `signals[i]` (same source list)."""
    raw = [
        _signal_dict(
            value="A", type_="competency", priority="required",
            weight=3, knockout=False, stage="screen",
        ),
        _signal_dict(
            value="B", type_="experience", priority="preferred",
            weight=2, knockout=False, stage="interview",
        ),
        _signal_dict(
            value="C", type_="behavioral", priority="preferred",
            weight=1, knockout=False, stage="interview",
        ),
    ]
    out = _project_signal_metadata(raw)
    assert [sm.value for sm in out] == ["A", "B", "C"]


def test_project_signal_metadata_empty_input():
    assert _project_signal_metadata([]) == []


# ---------------------------------------------------------------------------
# Integration test: build_session_config end-to-end with a real DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_session_config_populates_signal_metadata(db):
    """End-to-end: snapshot.signals JSONB → SessionConfig.signal_metadata.

    Mirrors the existing test_service.py setup; assertion is on
    signal_metadata field rather than question_kind.
    """
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await db.execute(sql_text(f"SET LOCAL app.current_tenant = '{tenant.id}'"))

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="UK Customer Support Engineer",
        description_raw="A" * 200,
        description_enriched="Enriched description for testing.",
        status="signals_confirmed",
        source="native",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        version=1,
        signals=[
            _signal_dict(
                value="UK shift", type_="experience", priority="required",
                weight=3, knockout=True, stage="screen",
                evaluation_method="verbal_response",
            ),
            _signal_dict(
                value="Conflict resolution", type_="behavioral",
                priority="preferred", weight=2, knockout=False,
                stage="interview", evaluation_method="behavioral_question",
            ),
            _signal_dict(
                value="Python", type_="competency", priority="preferred",
                weight=2, knockout=False, stage="screen",
                evaluation_method="verbal_response",
            ),
        ],
        seniority_level="senior",
        role_summary="Customer support engineer role for UK enterprise.",
        prompt_version="v1",
        confirmed_by=user.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()

    instance = JobPipelineInstance(tenant_id=tenant.id, job_posting_id=job.id)
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="Phone Screen",
        stage_type="phone_screen",
        duration_minutes=15,
        difficulty="medium",
        signal_filter={"include_types": ["competency", "experience", "behavioral"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="manual_review",
    )
    db.add(stage)
    await db.flush()

    bank = StageQuestionBank(
        tenant_id=tenant.id,
        stage_id=stage.id,
        job_posting_id=job.id,
        signal_snapshot_id=snapshot.id,
        status="confirmed",
        prompt_version="v1",
    )
    db.add(bank)
    await db.flush()

    rubric = {"excellent": "x" * 30, "meets_bar": "y" * 30, "below_bar": "z" * 30}
    question = StageQuestion(
        tenant_id=tenant.id,
        bank_id=bank.id,
        position=0,
        text="Can you work UK shift (1pm-9pm)?",
        signal_values=["UK shift"],
        estimated_minutes=1.5,
        is_mandatory=True,
        question_kind="compliance_binary",
        source="ai_generated",
        follow_ups=[],
        positive_evidence=["a", "b", "c"],
        red_flags=["d", "e"],
        rubric=rubric,
        evaluation_hint="evaluation hint at least 10 chars",
    )
    db.add(question)
    await db.flush()

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Charlie",
        email=f"charlie-{uuid.uuid4()}@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    assignment = CandidateJobAssignment(
        tenant_id=tenant.id,
        candidate_id=candidate.id,
        job_posting_id=job.id,
        current_stage_id=stage.id,
        assigned_by=user.id,
    )
    db.add(assignment)
    await db.flush()

    session = Session(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
    )
    db.add(session)
    await db.flush()

    config = await build_session_config(
        db, session_id=session.id, tenant_id=tenant.id,
    )

    # signals (flat values list — pre-existing field, unchanged)
    assert config.signals == ["UK shift", "Conflict resolution", "Python"]

    # signal_metadata aligned to signals, with strict typing
    assert len(config.signal_metadata) == 3
    sm_by_value = {sm.value: sm for sm in config.signal_metadata}

    assert sm_by_value["UK shift"].knockout is True
    assert sm_by_value["UK shift"].priority == "required"
    assert sm_by_value["UK shift"].weight == 3
    assert sm_by_value["UK shift"].stage == "screen"
    assert sm_by_value["UK shift"].evaluation_method == "verbal_response"

    assert sm_by_value["Conflict resolution"].knockout is False
    assert sm_by_value["Conflict resolution"].priority == "preferred"
    assert sm_by_value["Conflict resolution"].evaluation_method == "behavioral_question"

    assert sm_by_value["Python"].type == "competency"
    assert sm_by_value["Python"].weight == 2

    # Order preservation: signal_metadata[i] aligns with signals[i]
    assert [sm.value for sm in config.signal_metadata] == config.signals


@pytest.mark.asyncio
async def test_build_session_config_raises_on_empty_signal_metadata(db):
    """Engine-boundary fence: a confirmed snapshot whose `signals` JSONB
    is empty (or only off-spec rows that all get dropped) must NOT
    produce a SessionConfig with an empty signal_metadata. Upstream
    `ExtractedSignals.signals` enforces min_length=5 so this is
    unreachable in production, but a single bad row passing through
    silently would degrade into an orchestrator with no signals to
    track. Loud-fail at session start.

    Synthesized by writing a snapshot row with `signals=[]` directly
    via the ORM — bypasses the extraction validator that would
    normally enforce min_length=5.
    """
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await db.execute(sql_text(f"SET LOCAL app.current_tenant = '{tenant.id}'"))

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Test Role",
        description_raw="A" * 200,
        description_enriched="Enriched description for testing.",
        status="signals_confirmed",
        source="native",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()

    # Synthetic empty-signals snapshot (bypasses upstream min_length=5).
    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        version=1,
        signals=[],
        seniority_level="senior",
        role_summary="Role summary for testing.",
        prompt_version="v1",
        confirmed_by=user.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()

    instance = JobPipelineInstance(tenant_id=tenant.id, job_posting_id=job.id)
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="Phone Screen",
        stage_type="phone_screen",
        duration_minutes=15,
        difficulty="medium",
        signal_filter={},
        pass_criteria={},
        advance_behavior="manual_review",
    )
    db.add(stage)
    await db.flush()

    bank = StageQuestionBank(
        tenant_id=tenant.id,
        stage_id=stage.id,
        job_posting_id=job.id,
        signal_snapshot_id=snapshot.id,
        status="confirmed",
        prompt_version="v1",
    )
    db.add(bank)
    await db.flush()

    rubric = {"excellent": "x" * 30, "meets_bar": "y" * 30, "below_bar": "z" * 30}
    question = StageQuestion(
        tenant_id=tenant.id,
        bank_id=bank.id,
        position=0,
        text="Tell me about yourself.",
        signal_values=["something"],
        estimated_minutes=2.0,
        is_mandatory=True,
        question_kind="technical_scenario",
        source="ai_generated",
        follow_ups=[],
        positive_evidence=["a", "b", "c"],
        red_flags=["d", "e"],
        rubric=rubric,
        evaluation_hint="evaluation hint at least 10 chars",
    )
    db.add(question)
    await db.flush()

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Charlie",
        email=f"charlie-{uuid.uuid4()}@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    assignment = CandidateJobAssignment(
        tenant_id=tenant.id,
        candidate_id=candidate.id,
        job_posting_id=job.id,
        current_stage_id=stage.id,
        assigned_by=user.id,
    )
    db.add(assignment)
    await db.flush()

    session = Session(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
    )
    db.add(session)
    await db.flush()

    with pytest.raises(EmptySignalMetadataError, match="empty signal_metadata"):
        await build_session_config(
            db, session_id=session.id, tenant_id=tenant.id,
        )


@pytest.mark.asyncio
async def test_build_session_config_populates_job_and_candidate_ids(db):
    """A.1 part 2 — `build_session_config` projects `job_id` and
    `candidate_id` from the walked rows so `InterviewState` can be
    constructed with required identity fields.
    """
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", **_VALID_PROFILE,
    )
    tenant.super_admin_id = user.id
    await db.flush()
    await db.execute(sql_text(f"SET LOCAL app.current_tenant = '{tenant.id}'"))

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Identity Fields Engineer",
        description_raw="A" * 200,
        description_enriched="Enriched description for identity field test.",
        status="signals_confirmed",
        source="native",
        created_by=user.id,
    )
    db.add(job)
    await db.flush()

    snapshot = JobPostingSignalSnapshot(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        version=1,
        signals=[
            _signal_dict(
                value="Python", type_="competency", priority="required",
                weight=3, knockout=False, stage="screen",
                evaluation_method="verbal_response",
            ),
        ],
        seniority_level="mid",
        role_summary="Role summary for identity field test.",
        prompt_version="v1",
        confirmed_by=user.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(snapshot)
    await db.flush()

    instance = JobPipelineInstance(tenant_id=tenant.id, job_posting_id=job.id)
    db.add(instance)
    await db.flush()

    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="AI Screen",
        stage_type="ai_screening",
        duration_minutes=20,
        difficulty="medium",
        signal_filter={},
        pass_criteria={},
        advance_behavior="manual_review",
    )
    db.add(stage)
    await db.flush()

    bank = StageQuestionBank(
        tenant_id=tenant.id,
        stage_id=stage.id,
        job_posting_id=job.id,
        signal_snapshot_id=snapshot.id,
        status="confirmed",
        prompt_version="v1",
    )
    db.add(bank)
    await db.flush()

    rubric = {"excellent": "x" * 30, "meets_bar": "y" * 30, "below_bar": "z" * 30}
    question = StageQuestion(
        tenant_id=tenant.id,
        bank_id=bank.id,
        position=0,
        text="Tell me about your Python experience in depth.",
        signal_values=["Python"],
        estimated_minutes=2.0,
        is_mandatory=True,
        question_kind="technical_scenario",
        source="ai_generated",
        follow_ups=[],
        positive_evidence=["a", "b", "c"],
        red_flags=["d", "e"],
        rubric=rubric,
        evaluation_hint="evaluation hint at least 10 chars",
    )
    db.add(question)
    await db.flush()

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Dana",
        email=f"dana-{uuid.uuid4()}@example.com",
        source="manual",
        created_by=user.id,
    )
    db.add(candidate)
    await db.flush()

    assignment = CandidateJobAssignment(
        tenant_id=tenant.id,
        candidate_id=candidate.id,
        job_posting_id=job.id,
        current_stage_id=stage.id,
        assigned_by=user.id,
    )
    db.add(assignment)
    await db.flush()

    session = Session(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
    )
    db.add(session)
    await db.flush()

    config = await build_session_config(
        db, session_id=session.id, tenant_id=tenant.id,
    )

    assert config.job_id == str(job.id), (
        f"expected job_id={job.id!s}, got {config.job_id!r}"
    )
    assert config.candidate_id == str(candidate.id), (
        f"expected candidate_id={candidate.id!s}, got {config.candidate_id!r}"
    )
