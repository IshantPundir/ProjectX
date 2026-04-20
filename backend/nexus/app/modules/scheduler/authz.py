"""Scheduler module authz.

Nothing here is RBAC — that's handled by the router via require_candidate_access
and require_job_access. What lives here: stage-type + assignment-status guards
that apply to the invite-dispatch path.
"""
from __future__ import annotations

from app.models import CandidateJobAssignment, JobPipelineStage
from app.modules.scheduler.errors import (
    AssignmentNotActiveError,
    InvalidStageTypeForInviteError,
)


def assert_assignment_active(assignment: CandidateJobAssignment) -> None:
    if assignment.status != "active":
        raise AssignmentNotActiveError()


def assert_stage_is_ai_interview(stage: JobPipelineStage) -> None:
    if stage.stage_type != "ai_interview":
        raise InvalidStageTypeForInviteError(stage_type=stage.stage_type)
