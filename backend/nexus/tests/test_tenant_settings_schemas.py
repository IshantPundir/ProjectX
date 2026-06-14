"""Pure-unit tests for app.modules.tenant_settings.schemas."""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.modules.tenant_settings import TenantSettings


def test_defaults() -> None:
    tenant_id = uuid.uuid4()
    s = TenantSettings(tenant_id=tenant_id)
    assert s.tenant_id == tenant_id
    assert s.engine_agent_name is None


def test_explicit_values() -> None:
    tenant_id = uuid.uuid4()
    s = TenantSettings(
        tenant_id=tenant_id,
        engine_agent_name="Acme-Bot",
    )
    assert s.engine_agent_name == "Acme-Bot"


def test_round_trip() -> None:
    tenant_id = uuid.uuid4()
    s = TenantSettings(
        tenant_id=tenant_id,
        engine_agent_name=None,
    )
    dumped = s.model_dump(mode="json")
    s2 = TenantSettings.model_validate(dumped)
    assert s2 == s


def test_empty_agent_name_rejected() -> None:
    """engine_agent_name must be None or non-empty — empty string would
    create a divergence between _agent_name_override_active (True) and
    the displayed name (env fallback)."""
    with pytest.raises(ValidationError):
        TenantSettings(
            tenant_id=uuid.uuid4(),
            engine_agent_name="",
        )


def test_whitespace_only_agent_name_rejected() -> None:
    """Whitespace-only is functionally empty."""
    with pytest.raises(ValidationError):
        TenantSettings(
            tenant_id=uuid.uuid4(),
            engine_agent_name="   ",
        )
