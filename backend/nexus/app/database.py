from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import sqlalchemy
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

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
            await session.execute(
                sqlalchemy.text("SET LOCAL app.current_tenant = :tid"),
                {"tid": tenant_id}
            )
            yield session


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Yield a plain session (for non-tenant-scoped operations like auth)."""
    async with async_session_factory() as session:
        async with session.begin():
            yield session
