"""Test fixtures — integration tests against a real PostgreSQL database.

Uses connection-level transaction rollback so each test is fully isolated
without needing to truncate tables.
"""

import os
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
import sqlalchemy
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.database import Base
from app.main import app
from app.models import Client, OrganizationalUnit, User

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/projectx_test",
)

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)


@pytest.fixture(scope="session")
def event_loop_policy():
    """Use default event loop policy for the whole test session."""
    import asyncio

    return asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _create_tables():
    """Create all tables once at test session start."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    # Drop all tables via raw SQL with CASCADE to handle circular FK between
    # clients.super_admin_id → users and users.tenant_id → clients.
    async with test_engine.begin() as conn:
        table_names = ", ".join(Base.metadata.tables.keys())
        await conn.execute(sqlalchemy.text(f"DROP TABLE IF EXISTS {table_names} CASCADE"))
    await test_engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def db(_create_tables: None):
    """Per-test database session with automatic rollback.

    The session is bound to a connection-level transaction.
    Everything the test does — including flushes and internal commits
    by service functions — is rolled back after the test.
    """
    async with test_engine.connect() as conn:
        txn = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await txn.rollback()


@pytest.fixture
async def client() -> AsyncClient:
    """Async HTTP test client for FastAPI (kept for existing tests)."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Factory helpers — sensible defaults, tests override only what matters
# ---------------------------------------------------------------------------

_counter = 0


def _next_id() -> int:
    global _counter
    _counter += 1
    return _counter


async def create_test_client(db: AsyncSession, **kwargs) -> Client:
    """Create a Client row with sensible defaults."""
    n = _next_id()
    defaults = {
        "name": f"Test Company {n}",
        "domain": f"test{n}.com",
        "industry": "Technology",
        "plan": "trial",
        "onboarding_complete": False,
    }
    defaults.update(kwargs)
    client = Client(**defaults)
    db.add(client)
    await db.flush()
    return client


async def create_test_user(db: AsyncSession, client_id: uuid.UUID, **kwargs) -> User:
    """Create a User row with sensible defaults."""
    n = _next_id()
    now = datetime.now(UTC)
    defaults = {
        "auth_user_id": uuid.uuid4(),
        "tenant_id": client_id,
        "email": f"user{n}@test.com",
        "full_name": f"Test User {n}",
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    user = User(**defaults)
    db.add(user)
    await db.flush()
    return user


async def create_test_org_unit(
    db: AsyncSession, client_id: uuid.UUID, **kwargs
) -> OrganizationalUnit:
    """Create an OrganizationalUnit row with sensible defaults."""
    n = _next_id()
    now = datetime.now(UTC)
    defaults = {
        "client_id": client_id,
        "name": f"Test Unit {n}",
        "unit_type": "division",
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    unit = OrganizationalUnit(**defaults)
    db.add(unit)
    await db.flush()
    return unit
