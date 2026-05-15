"""Phase 2C.1 — Pipeline Builder module.

Owns pipeline templates (per org unit) and job pipeline instances (per job).
Called from jd.confirm_signals() via auto_apply_pipeline_on_confirmation().

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
from app.modules.pipelines.service import (
    auto_apply_pipeline_on_confirmation,
    ensure_minimal_pipeline_for_job,
)

__all__ = [
    "JobPipelineInstance",
    "JobPipelineStage",
    "PipelineStageParticipant",
    "PipelineTemplate",
    "PipelineTemplateStage",
    "auto_apply_pipeline_on_confirmation",
    "bank_eligible_stage_types",
    "ensure_minimal_pipeline_for_job",
    "human_led_stage_types",
    "is_paused",
    "middle_stage_types_for_activation",
]
