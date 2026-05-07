"""Service-layer tests for app.modules.tenant_settings.service."""
from __future__ import annotations

import uuid

from app.modules.tenant_settings import (
    DEFAULT_TENANT_SETTINGS,
    TenantSettings,
    get_tenant_settings,
)
from app.modules.tenant_settings.models import TenantSettingsModel
from tests.conftest import create_test_client


async def test_no_row_returns_defaults(db) -> None:
    tenant = await create_test_client(db)
    s = await get_tenant_settings(db, tenant.id)
    assert s == DEFAULT_TENANT_SETTINGS(tenant.id)


async def test_existing_row_returns_values(db) -> None:
    tenant = await create_test_client(db)
    db.add(
        TenantSettingsModel(
            tenant_id=tenant.id,
            engine_knockout_policy="close_polite",
            engine_agent_name="Acme-Bot",
        )
    )
    await db.flush()
    s = await get_tenant_settings(db, tenant.id)
    assert s.engine_knockout_policy == "close_polite"
    assert s.engine_agent_name == "Acme-Bot"
    assert s.tenant_id == tenant.id


async def test_partial_row_only_engine_agent_name(db) -> None:
    """A row with only engine_agent_name set keeps default policy."""
    tenant = await create_test_client(db)
    db.add(
        TenantSettingsModel(
            tenant_id=tenant.id,
            engine_agent_name="Acme-Bot",
            # engine_knockout_policy uses server_default = 'close_polite'
        )
    )
    await db.flush()
    s = await get_tenant_settings(db, tenant.id)
    assert s.engine_knockout_policy == "close_polite"
    assert s.engine_agent_name == "Acme-Bot"


async def test_default_factory_returns_correct_tenant_id() -> None:
    """DEFAULT_TENANT_SETTINGS is keyed on the requesting tenant_id."""
    tenant_id = uuid.uuid4()
    s = DEFAULT_TENANT_SETTINGS(tenant_id)
    assert s.tenant_id == tenant_id
    assert s.engine_knockout_policy == "close_polite"
    assert s.engine_agent_name is None
