"""Pure-unit tests for app.modules.tenant_settings.schemas."""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.modules.tenant_settings import KnockoutPolicy, TenantSettings


def test_defaults() -> None:
    tenant_id = uuid.uuid4()
    s = TenantSettings(tenant_id=tenant_id)
    assert s.tenant_id == tenant_id
    assert s.engine_knockout_policy == "record_only"
    assert s.engine_agent_name is None


def test_explicit_values() -> None:
    tenant_id = uuid.uuid4()
    s = TenantSettings(
        tenant_id=tenant_id,
        engine_knockout_policy="close_polite",
        engine_agent_name="Acme-Bot",
    )
    assert s.engine_knockout_policy == "close_polite"
    assert s.engine_agent_name == "Acme-Bot"


def test_unknown_policy_rejected() -> None:
    with pytest.raises(ValidationError):
        TenantSettings(
            tenant_id=uuid.uuid4(),
            engine_knockout_policy="hard_reject",  # not in Literal
        )


def test_round_trip() -> None:
    tenant_id = uuid.uuid4()
    s = TenantSettings(
        tenant_id=tenant_id,
        engine_knockout_policy="close_polite",
        engine_agent_name=None,
    )
    dumped = s.model_dump(mode="json")
    s2 = TenantSettings.model_validate(dumped)
    assert s2 == s


def test_knockout_policy_literal_values() -> None:
    """Type-level: KnockoutPolicy Literal exposes both values."""
    # Runtime check via __args__
    from typing import get_args
    assert set(get_args(KnockoutPolicy)) == {"record_only", "close_polite"}
