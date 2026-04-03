"""Tests for database session helpers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database import get_bypass_session


@pytest.mark.asyncio
async def test_bypass_session_sets_rls_flag():
    """get_bypass_session must SET LOCAL app.bypass_rls = 'true'."""
    mock_session = AsyncMock()
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin.return_value.__aenter__ = AsyncMock(return_value=None)
    mock_session.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock(return_value=mock_session)

    with patch("app.database.async_session_factory", mock_factory):
        async with get_bypass_session() as session:
            pass

    # Verify SET LOCAL was called
    mock_session.execute.assert_called_once()
    call_args = mock_session.execute.call_args
    sql_text = str(call_args[0][0])
    assert "app.bypass_rls" in sql_text
    assert "true" in sql_text.lower()
