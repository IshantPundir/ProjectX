"""Pydantic models for the tenant_settings module."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class TenantSettings(BaseModel):
    """Per-tenant engine configuration.

    `engine_agent_name` is None-able; null means "use the env fallback
    `settings.engine_agent_name`". The override applies only at the
    candidate-facing prompt-substitution site (`controller.py`'s
    `build_controller_prompt`) and the `controller.started` log; the
    LiveKit routing label (decorator at `agent.py:130` and
    `dispatch_agent` call at `livekit.py:102`) STAYS on the env value
    because it's a fleet-wide routing primitive, not a candidate-facing
    identifier (P5-Q1 in the Phase 5 spec).
    """

    tenant_id: UUID
    engine_agent_name: str | None = None
    proctoring_enabled: bool = True
    proctoring_soft_violation_limit: int = Field(default=3, ge=1, le=20)
    proctoring_fullscreen_grace_seconds: int = Field(default=10, ge=3, le=60)

    @field_validator("engine_agent_name")
    @classmethod
    def _reject_empty_override(cls, v: str | None) -> str | None:
        """None means 'use env fallback'; a string means 'tenant override'.
        Empty / whitespace-only strings are neither — reject so the
        controller's _agent_name_override_active flag never disagrees
        with the displayed name.
        """
        if v is not None and not v.strip():
            raise ValueError(
                "engine_agent_name override must be non-empty; use None for env fallback"
            )
        return v
