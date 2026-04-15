"""Tests for database session helpers + DB_RUNTIME_ROLE role switching."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database import _apply_runtime_role, get_bypass_session


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
        async with get_bypass_session() as session:  # noqa: F841
            pass

    # Conftest forces DB_RUNTIME_ROLE="" so _apply_runtime_role is a no-op.
    # Only the bypass_rls SET LOCAL should be executed.
    mock_session.execute.assert_called_once()
    sql_text = str(mock_session.execute.call_args[0][0])
    assert "app.bypass_rls" in sql_text
    assert "true" in sql_text.lower()


@pytest.mark.asyncio
async def test_apply_runtime_role_noop_when_unset():
    """_apply_runtime_role must not touch the session when role is None."""
    mock_session = AsyncMock()
    with patch("app.database.settings") as mock_settings:
        mock_settings.db_runtime_role = None
        await _apply_runtime_role(mock_session)
    mock_session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_apply_runtime_role_issues_set_local_role():
    """_apply_runtime_role must issue SET LOCAL ROLE when configured."""
    mock_session = AsyncMock()
    with patch("app.database.settings") as mock_settings:
        mock_settings.db_runtime_role = "nexus_app"
        await _apply_runtime_role(mock_session)
    mock_session.execute.assert_called_once()
    sql_text = str(mock_session.execute.call_args[0][0])
    assert "SET LOCAL ROLE nexus_app" in sql_text


def test_db_runtime_role_validator_rejects_injection():
    """Settings.db_runtime_role must reject non-identifier values.

    A malformed DB_RUNTIME_ROLE would be interpolated into a
    `SET LOCAL ROLE <value>` statement (asyncpg can't parameterise
    session-setting commands), so validation at config load time is
    the last line of defence.
    """
    from app.config import Settings

    for bad in (
        "nexus_app; DROP TABLE users",
        "nexus_app'",
        "nexus app",
        "1nexus",
        "-postgres",
    ):
        with pytest.raises(ValueError, match="DB_RUNTIME_ROLE"):
            Settings(db_runtime_role=bad)


def test_db_runtime_role_validator_accepts_valid():
    """Valid PG identifiers must pass."""
    from app.config import Settings

    for good in ("nexus_app", "app_user", "NEXUS_1", "_internal"):
        s = Settings(db_runtime_role=good)
        assert s.db_runtime_role == good


def test_db_runtime_role_validator_treats_empty_as_none():
    """Empty string must normalise to None (disables role switching)."""
    from app.config import Settings

    s = Settings(db_runtime_role="")
    assert s.db_runtime_role is None
