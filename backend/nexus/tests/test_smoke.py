"""Smoke test — validates test infrastructure works."""

import pytest

from tests.conftest import create_test_client, create_test_org_unit, create_test_user


@pytest.mark.asyncio
async def test_factory_helpers_create_rows(db):
    client = await create_test_client(db, name="Acme Corp")
    user = await create_test_user(db, client.id, email="alice@acme.com")
    unit = await create_test_org_unit(db, client.id, name="Engineering")

    assert client.id is not None
    assert user.tenant_id == client.id
    assert unit.client_id == client.id
    assert user.email == "alice@acme.com"
    assert unit.name == "Engineering"
