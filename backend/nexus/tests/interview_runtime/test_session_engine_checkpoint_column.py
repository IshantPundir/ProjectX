"""Smoke test: sessions.engine_checkpoint column mapped correctly in ORM."""
import pytest
from sqlalchemy import select

from app.modules.session.models import Session


@pytest.mark.asyncio
async def test_engine_checkpoint_column_exists(db):
    """Smoke test that the model maps the column without error."""
    stmt = select(Session.engine_checkpoint).limit(0)
    await db.execute(stmt)
