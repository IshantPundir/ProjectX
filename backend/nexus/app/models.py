"""Transitional re-export shim — Phase 4a of the modular-monolith refactor.

Each domain module now owns its ORM classes in `app/modules/<m>/models.py`.
This shim keeps `from app.models import X` working until Stage E (Task 22)
rewrites every cross-module import to use the per-module public API.

DO NOT add new model classes here. Add them to the owning module's
`models.py` and re-export through the module's `__init__.py`.

This file is deleted in Stage E (sub-commit 4d-2).
"""

# Re-exports — keep in alphabetical order per owning module so future
# diffs stay readable.

# auth
from app.modules.auth.models import User, UserInvite, UserRoleAssignment

# audit
from app.modules.audit.models import AuditLog

# candidates
from app.modules.candidates.models import (
    Candidate,
    CandidateJobAssignment,
    CandidateStageProgress,
)

# jd
from app.modules.jd.models import JobPosting, JobPostingSignalSnapshot

# org_units
from app.modules.org_units.models import Client, OrganizationalUnit

# pipelines
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
    PipelineStageParticipant,
    PipelineTemplate,
    PipelineTemplateStage,
)

# question_bank
from app.modules.question_bank.models import StageQuestion, StageQuestionBank

# roles
from app.modules.roles.models import Role

# session
from app.modules.session.models import CandidateSessionToken, Session

__all__ = [
    "AuditLog",
    "Candidate",
    "CandidateJobAssignment",
    "CandidateSessionToken",
    "CandidateStageProgress",
    "Client",
    "JobPipelineInstance",
    "JobPipelineStage",
    "JobPosting",
    "JobPostingSignalSnapshot",
    "OrganizationalUnit",
    "PipelineStageParticipant",
    "PipelineTemplate",
    "PipelineTemplateStage",
    "Role",
    "Session",
    "StageQuestion",
    "StageQuestionBank",
    "User",
    "UserInvite",
    "UserRoleAssignment",
]
