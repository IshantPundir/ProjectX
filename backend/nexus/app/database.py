from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import sqlalchemy
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from starlette.requests import Request

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_size=20,
    max_overflow=10,
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


@asynccontextmanager
async def get_tenant_session(tenant_id: str) -> AsyncGenerator[AsyncSession]:
    """Yield a session with RLS tenant context set."""
    async with async_session_factory() as session:
        async with session.begin():
            # SET LOCAL doesn't support parameterized queries in asyncpg.
            # tenant_id is a UUID from the validated JWT — safe to interpolate.
            await session.execute(
                sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
            )
            yield session


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Yield a plain session (for non-tenant-scoped operations like auth)."""
    async with async_session_factory() as session:
        async with session.begin():
            yield session


@asynccontextmanager
async def get_bypass_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a session with RLS bypass enabled.

    Used ONLY for: admin routes, complete-invite, and onboarding completion.
    Never use on endpoints reachable by regular client users.
    """
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(
                sqlalchemy.text("SET LOCAL app.bypass_rls = 'true'")
            )
            yield session


async def get_bypass_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session with RLS bypass.

    Use with: Depends(get_bypass_db)
    Only for: admin routes, complete-invite, onboarding completion.
    """
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(
                sqlalchemy.text("SET LOCAL app.bypass_rls = 'true'")
            )
            yield session


async def get_tenant_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session with RLS tenant context.

    Use with: Depends(get_tenant_db)
    For: all tenant-scoped routes.
    """
    tenant_id = request.state.tenant_id
    async with async_session_factory() as session:
        async with session.begin():
            # SET LOCAL doesn't support parameterized queries in asyncpg.
            # tenant_id is a UUID from the validated JWT — safe to interpolate.
            await session.execute(
                sqlalchemy.text(f"SET LOCAL app.current_tenant = '{tenant_id}'")
            )
            yield session
