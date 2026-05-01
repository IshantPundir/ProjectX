"""Phase 2C.1 — Pipeline Builder module.

Owns pipeline templates (per org unit) and job pipeline instances (per job).
Called from jd.confirm_signals() via auto_apply_pipeline_on_confirmation().

Public surface for cross-module callers.

NOTE: ``auto_apply_pipeline_on_confirmation`` and the
``categories`` helpers are DEFERRED to Stage E.2 (sub-commit 4d-2).
They cannot be eagerly imported here while ``app/models.py`` is still
a re-export shim — see auth/__init__.py for the cycle explanation.
Removing the shim in 4d-2 lets us add them.
"""
from app.modules.pipelines.models import (
    JobPipelineInstance,
    JobPipelineStage,
    PipelineStageParticipant,
    PipelineTemplate,
    PipelineTemplateStage,
)

__all__ = [
    "JobPipelineInstance",
    "JobPipelineStage",
    "PipelineStageParticipant",
    "PipelineTemplate",
    "PipelineTemplateStage",
]
