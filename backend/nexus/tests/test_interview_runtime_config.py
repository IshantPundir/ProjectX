"""build_session_config — happy + reject paths.

Verifies: stage-type allowlist (ai_screening + phone_screen), bank readiness
gate (status='ready' AND is_stale=False), cross-tenant isolation, missing
company_profile rejection, mandatory-question ordering, and the PII
guard (CandidateContext serialization has no email field).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import update

from app.models import (
    Candidate,
    CandidateJobAssignment,
    JobPipelineInstance,
    JobPipelineStage,
    JobPosting,
    JobPostingSignalSnapshot,
    OrganizationalUnit,
    Session as SessionRow,
    StageQuestion,
    StageQuestionBank,
)
from app.modules.interview_runtime.errors import (
    CompanyProfileMissingError,
    QuestionBankNotReadyError,
    StageNotAiDrivenError,
)
from app.modules.interview_runtime.service import _AI_STAGE_TYPES, build_session_config
from tests.conftest import create_test_client, create_test_org_unit, create_test_user

pytestmark = pytest.mark.asyncio


_VALID_PROFILE = {
    "about": "We build distributed inference infra for mid-market AI startups.",
    "industry": "ai_machine_learning",
    "company_stage": "series_a_b",
    "hiring_bar": "Engineers who own problems end-to-end with high autonomy.",
}

# Sentinel: distinguishes "use default profile" (None arg) from "seed an org_unit
# with NO profile" (this sentinel). object() is used so identity checks are exact.
_NO_PROFILE: object = object()


def _signal(value: str, *, stage: str = "screen") -> dict:
    """Minimal valid signal-shape dict for a snapshot.signals JSONB list element."""
    return {
        "value": value,
        "type": "competency",
        "priority": "required",
        "weight": 2,
        "knockout": False,
        "stage": stage,
        "evaluation_method": "verification",
        "evaluation_hint": None,
        "source": "ai_extracted",
        "inference_basis": None,
    }


def _valid_rubric() -> dict:
    return {
        "excellent": "A strong answer names specific tools and describes hypothesis-verify flow.",
        "meets_bar": "An acceptable answer mentions at least one tool and shows structure.",
        "below_bar": "A weak answer is vague with no tools and no structure.",
    }


async def _seed_full_session_chain(
    db,
    *,
    stage_type: str = "ai_screening",
    bank_status: str = "ready",
    bank_is_stale: bool = False,
    company_profile: object = None,
    snapshot_confirmed: bool = True,
    n_questions: int = 3,
    n_mandatory: int = 1,
) -> tuple[UUID, UUID]:
    """Seed the full FK chain build_session_config walks.

    Returns ``(session_id, tenant_id)``.

    ``company_profile`` controls what is stored on the org_unit:
    - ``None`` (default) → use ``_VALID_PROFILE``
    - ``_NO_PROFILE`` sentinel → seed the org_unit with ``company_profile=None``
      (negative-test path: missing company profile)

    Graph: company-unit (root) -> job_posting -> pipeline instance ->
    stage -> bank -> N questions, plus candidate + assignment + session
    pinned to the stage. Snapshot is created confirmed by default.
    """
    # Resolve which profile dict to pass to the ORM
    if company_profile is _NO_PROFILE:
        profile_to_set: dict | None = None
    elif company_profile is None:
        profile_to_set = _VALID_PROFILE
    else:
        profile_to_set = company_profile  # type: ignore[assignment]

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    company = await create_test_org_unit(
        db, tenant.id, unit_type="company", company_profile=profile_to_set,
    )
    tenant.super_admin_id = user.id
    await db.flush()

    job = JobPosting(
        tenant_id=tenant.id,
        org_unit_id=company.id,
        title="Senior ML Engineer",
        description_raw="A" * 200,
        description_enriched="Enriched JD body.",
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
        signals=[_signal("Python"), _signal("PyTorch", stage="interview")],
        seniority_level="senior",
        role_summary="A senior ML engineer who can ship inference pipelines.",
        prompt_version="v1",
        confirmed_by=user.id if snapshot_confirmed else None,
        confirmed_at=datetime.now(UTC) if snapshot_confirmed else None,
    )
    db.add(snapshot)
    await db.flush()

    instance = JobPipelineInstance(
        tenant_id=tenant.id,
        job_posting_id=job.id,
        source_template_id=None,
    )
    db.add(instance)
    await db.flush()

    # phone_screen + ai_screening stages require non-null duration / difficulty /
    # signal_filter / pass_criteria / advance_behavior.
    stage = JobPipelineStage(
        tenant_id=tenant.id,
        instance_id=instance.id,
        position=0,
        name="Screening",
        stage_type=stage_type,
        duration_minutes=30,
        difficulty="medium",
        signal_filter={"include_types": ["competency"]},
        pass_criteria={"type": "all_knockouts_pass"},
        advance_behavior="auto_advance",
    )
    db.add(stage)
    await db.flush()

    bank = StageQuestionBank(
        tenant_id=tenant.id,
        stage_id=stage.id,
        job_posting_id=job.id,
        signal_snapshot_id=snapshot.id,
        status=bank_status,
        is_stale=bank_is_stale,
        prompt_version="v1",
    )
    db.add(bank)
    await db.flush()

    # Create N questions; the first n_mandatory are mandatory.
    for i in range(n_questions):
        q = StageQuestion(
            tenant_id=tenant.id,
            bank_id=bank.id,
            position=i,
            source="ai_generated",
            text=(
                f"Question {i}: walk me through a recent "
                f"{'mandatory' if i < n_mandatory else 'optional'} project "
                f"you shipped end-to-end."
            ),
            signal_values=["Python"],
            estimated_minutes=5,
            is_mandatory=(i < n_mandatory),
            follow_ups=["What tools did you use?"],
            positive_evidence=[
                "Names specific tools",
                "Describes hypothesis-verify",
                "Mentions post-mortem",
            ],
            red_flags=["No specific tools", "Blames team"],
            rubric=_valid_rubric(),
            evaluation_hint="Strong answer names tools, describes structured approach.",
        )
        db.add(q)
    await db.flush()

    candidate = Candidate(
        tenant_id=tenant.id,
        name="Alex Test",
        email=f"alex-{uuid.uuid4()}@example.com",
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

    sess = SessionRow(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
    )
    db.add(sess)
    await db.flush()

    return sess.id, tenant.id


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_happy_path_ai_screening(db):
    """build_session_config returns a valid SessionConfig for an ai_screening stage."""
    session_id, tenant_id = await _seed_full_session_chain(db, stage_type="ai_screening")

    config = await build_session_config(db, session_id=session_id, tenant_id=tenant_id)

    # Wire-contract checks
    assert config.session_id == str(session_id)
    assert config.candidate.name == "Alex Test"
    assert config.stage.stage_type == "ai_screening"
    assert config.stage.duration_minutes == 30
    assert config.stage.difficulty == "medium"
    assert len(config.stage.questions) == 3
    assert config.role_summary
    assert config.seniority_level == "senior"
    assert config.signals == ["Python", "PyTorch"]

    # PII guard — CandidateContext must NOT serialize an email field.
    assert "email" not in config.candidate.model_dump()
    # And the model itself doesn't define one.
    assert not hasattr(config.candidate, "email")


async def test_happy_path_phone_screen_also_allowed(db):
    """phone_screen is the second allowlisted stage type."""
    session_id, tenant_id = await _seed_full_session_chain(db, stage_type="phone_screen")
    config = await build_session_config(db, session_id=session_id, tenant_id=tenant_id)
    assert config.stage.stage_type == "phone_screen"


async def test_mandatory_questions_sort_first(db):
    """Mandatory questions appear before optional ones in the rendered list."""
    session_id, tenant_id = await _seed_full_session_chain(
        db, n_questions=5, n_mandatory=2,
    )
    config = await build_session_config(db, session_id=session_id, tenant_id=tenant_id)
    # The first n_mandatory questions are mandatory; the rest aren't.
    seen_optional = False
    for q in config.stage.questions:
        if not q.is_mandatory:
            seen_optional = True
        elif seen_optional:
            pytest.fail(
                f"mandatory question at position {q.position} appears after an optional one"
            )


# ---------------------------------------------------------------------------
# Stage-type allowlist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stage_type",
    [s for s in ("intake", "human_interview", "debrief", "take_home") if s not in _AI_STAGE_TYPES],
)
async def test_non_ai_stage_types_rejected(db, stage_type):
    """All v5 stage types outside the allowlist raise StageNotAiDrivenError."""
    session_id, tenant_id = await _seed_full_session_chain(db, stage_type=stage_type)
    with pytest.raises(StageNotAiDrivenError) as excinfo:
        await build_session_config(db, session_id=session_id, tenant_id=tenant_id)
    assert excinfo.value.stage_type == stage_type


# ---------------------------------------------------------------------------
# Bank readiness
# ---------------------------------------------------------------------------


async def test_bank_generating_rejected(db):
    session_id, tenant_id = await _seed_full_session_chain(db, bank_status="generating")
    with pytest.raises(QuestionBankNotReadyError):
        await build_session_config(db, session_id=session_id, tenant_id=tenant_id)


async def test_bank_draft_rejected(db):
    session_id, tenant_id = await _seed_full_session_chain(db, bank_status="draft")
    with pytest.raises(QuestionBankNotReadyError):
        await build_session_config(db, session_id=session_id, tenant_id=tenant_id)


async def test_bank_stale_rejected(db):
    session_id, tenant_id = await _seed_full_session_chain(
        db, bank_status="ready", bank_is_stale=True,
    )
    with pytest.raises(QuestionBankNotReadyError):
        await build_session_config(db, session_id=session_id, tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------


async def test_cross_tenant_returns_not_found(db):
    """Calling with a different tenant_id surfaces as ValueError('session not found')."""
    session_id, _real_tenant = await _seed_full_session_chain(db)
    other_tenant = uuid.uuid4()
    with pytest.raises(ValueError, match="not found"):
        await build_session_config(db, session_id=session_id, tenant_id=other_tenant)


async def test_unknown_session_returns_not_found(db):
    """Random session_id under a real tenant also returns not found."""
    _seed_session_id, tenant_id = await _seed_full_session_chain(db)
    with pytest.raises(ValueError, match="not found"):
        await build_session_config(db, session_id=uuid.uuid4(), tenant_id=tenant_id)


# ---------------------------------------------------------------------------
# Company profile
# ---------------------------------------------------------------------------


async def test_missing_company_profile_rejected(db):
    """If the org_unit ancestry has no company_profile, raise CompanyProfileMissingError."""
    session_id, tenant_id = await _seed_full_session_chain(
        db, company_profile=_NO_PROFILE,
    )
    with pytest.raises(CompanyProfileMissingError):
        await build_session_config(db, session_id=session_id, tenant_id=tenant_id)
