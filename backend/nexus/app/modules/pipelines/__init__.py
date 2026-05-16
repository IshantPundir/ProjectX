"""Phase 2C.1 — Pipeline Builder module.

Owns pipeline templates (per org unit) and job pipeline instances (per job).
`jd.confirm_signals` calls `ensure_minimal_pipeline_for_job` to auto-create
the bookend Intake → Debrief pipeline on signal confirmation; from there
the recruiter adds the middle stage(s) themselves before activating.

Public surface for cross-module callers.
"""
from app.modules.pipelines.categories import (
    bank_eligible_stage_types,
    human_led_stage_types,
    is_paused,
    middle_stage_types_for_activation,
)
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
    PipelineStageParticipant,
    PipelineTemplate,
    PipelineTemplateStage,
)
from app.modules.pipelines.service import ensure_minimal_pipeline_for_job

__all__ = [
    "JobPipelineInstance",
    "JobPipelineStage",
    "PipelineStageParticipant",
    "PipelineTemplate",
    "PipelineTemplateStage",
    "bank_eligible_stage_types",
    "ensure_minimal_pipeline_for_job",
    "human_led_stage_types",
    "is_paused",
    "middle_stage_types_for_activation",
]
