"""Tests for the candidate reference-photo upload endpoint + service.

Coverage:
  Service-level (unit, mocked storage):
    - save_reference_photo uploads bytes and stamps the two columns.
    - save_reference_photo raises SessionNotFoundError on wrong tenant.
    - R2 key is tenant+session scoped with the right extension per content_type.

  HTTP-level (integration via AsyncClient + candidate-token middleware):
    - Valid multipart upload (image/jpeg) returns 204 and sets the DB columns.
    - Unsupported content type returns 415.
    - Empty file returns 400.
"""
from __future__ import annotations

import io
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.modules.session.errors import SessionNotFoundError
from app.modules.session.models import CandidateSessionToken, Session as SessionRow
from app.modules.session import service as session_service
from app.modules.auth.service import create_candidate_token
from tests.conftest import (
    create_test_client,
    create_test_user,
    make_assignment_with_stage,
    seed_minimal_session,
    mint_candidate_session_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_storage() -> MagicMock:
    """Return a mock ObjectStorage whose upload_bytes is a no-op async call."""
    storage = MagicMock()
    storage.upload_bytes = AsyncMock(return_value=None)
    return storage


def _patch_storage(fake_storage: MagicMock):
    """Patch get_object_storage inside the session service module."""
    return patch(
        "app.modules.session.service.get_object_storage",
        return_value=fake_storage,
    )


def _patch_bypass_session_to(db: AsyncSession):
    """Redirect the middleware's bypass session to the test's rolled-back connection."""

    @asynccontextmanager
    async def _fake_bypass():
        yield db

    return patch("app.middleware.auth.get_bypass_session", _fake_bypass)


# ---------------------------------------------------------------------------
# Service-level tests (no HTTP, no middleware)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_reference_photo_uploads_and_stamps_columns(db: AsyncSession):
    """Happy path: bytes are uploaded and both columns are set."""
    session, tenant_id = await seed_minimal_session(db, state="pre_check")

    fake_storage = _make_fake_storage()
    image_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # minimal JPEG-like payload

    with _patch_storage(fake_storage):
        await session_service.save_reference_photo(
            db,
            session_id=session.id,
            tenant_id=tenant_id,
            data=image_bytes,
            content_type="image/jpeg",
        )

    # Storage was called exactly once with the expected key and content_type.
    fake_storage.upload_bytes.assert_awaited_once()
    call_args = fake_storage.upload_bytes.call_args
    key = call_args.args[0]
    assert key == f"reference-photos/{tenant_id}/{session.id}.jpg"
    assert call_args.kwargs["content_type"] == "image/jpeg"

    # ORM columns are stamped (flushed but not committed — still in test txn).
    await db.refresh(session)
    assert session.reference_photo_key == key
    assert session.reference_photo_captured_at is not None
    # Timestamp should be recent (within a few seconds of now).
    delta = datetime.now(UTC) - session.reference_photo_captured_at
    assert delta.total_seconds() < 5


@pytest.mark.asyncio
async def test_save_reference_photo_webp_extension(db: AsyncSession):
    """image/webp maps to .webp extension in the R2 key."""
    session, tenant_id = await seed_minimal_session(db, state="pre_check")
    fake_storage = _make_fake_storage()

    with _patch_storage(fake_storage):
        await session_service.save_reference_photo(
            db,
            session_id=session.id,
            tenant_id=tenant_id,
            data=b"webp-bytes",
            content_type="image/webp",
        )

    key = fake_storage.upload_bytes.call_args.args[0]
    assert key.endswith(".webp")


@pytest.mark.asyncio
async def test_save_reference_photo_wrong_tenant_raises(db: AsyncSession):
    """Cross-tenant: wrong tenant_id → SessionNotFoundError (same opacity as /state)."""
    session, _real_tenant_id = await seed_minimal_session(db, state="pre_check")
    other_tenant_id = uuid.uuid4()  # does not match the session's tenant
    fake_storage = _make_fake_storage()

    with _patch_storage(fake_storage):
        with pytest.raises(SessionNotFoundError):
            await session_service.save_reference_photo(
                db,
                session_id=session.id,
                tenant_id=other_tenant_id,
                data=b"some-bytes",
                content_type="image/jpeg",
            )

    # Storage must NOT have been called — we bail before uploading.
    fake_storage.upload_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_reference_photo_is_idempotent_overwrite(db: AsyncSession):
    """Second call with new bytes overwrites the key without error."""
    session, tenant_id = await seed_minimal_session(db, state="pre_check")
    fake_storage = _make_fake_storage()

    with _patch_storage(fake_storage):
        await session_service.save_reference_photo(
            db,
            session_id=session.id,
            tenant_id=tenant_id,
            data=b"first-upload",
            content_type="image/jpeg",
        )
        first_ts = (await db.get(SessionRow, session.id)).reference_photo_captured_at

        await session_service.save_reference_photo(
            db,
            session_id=session.id,
            tenant_id=tenant_id,
            data=b"second-upload",
            content_type="image/jpeg",
        )

    assert fake_storage.upload_bytes.await_count == 2
    await db.refresh(session)
    # Key is the same deterministic path.
    assert session.reference_photo_key == f"reference-photos/{tenant_id}/{session.id}.jpg"
    # Timestamp advances (or at least is non-null).
    assert session.reference_photo_captured_at is not None


# ---------------------------------------------------------------------------
# HTTP-level tests (full middleware + router pipeline)
# ---------------------------------------------------------------------------


async def _build_http_test_artifacts(db: AsyncSession):
    """Create session + token and return (session, token_str, tenant_id)."""
    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    assignment, stage = await make_assignment_with_stage(db, tenant, user)

    session = SessionRow(
        tenant_id=tenant.id,
        assignment_id=assignment.id,
        stage_id=stage.id,
        created_by=user.id,
    )
    db.add(session)
    await db.flush()

    token_str = await mint_candidate_session_token(
        db, session_id=session.id, tenant_id=tenant.id
    )
    return session, token_str, tenant.id


@pytest.mark.asyncio
async def test_http_upload_returns_204_and_sets_column(db: AsyncSession):
    """Valid multipart image/jpeg upload → 204 + reference_photo_key is set."""
    from app.database import get_tenant_db

    session, token_str, tenant_id = await _build_http_test_artifacts(db)

    fake_storage = _make_fake_storage()

    async def _override_db():
        yield db

    app.dependency_overrides[get_tenant_db] = _override_db
    try:
        with _patch_bypass_session_to(db), _patch_storage(fake_storage):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    f"/api/candidate-session/{token_str}/reference-photo",
                    files={"file": ("photo.jpg", io.BytesIO(b"\xff\xd8\xff" + b"\x00" * 50), "image/jpeg")},
                )
    finally:
        app.dependency_overrides.pop(get_tenant_db, None)

    assert response.status_code == 204, response.text
    fake_storage.upload_bytes.assert_awaited_once()
    # Verify the key was stored on the session row.
    await db.refresh(session)
    assert session.reference_photo_key is not None
    assert str(session.id) in session.reference_photo_key


@pytest.mark.asyncio
async def test_http_unsupported_content_type_returns_415(db: AsyncSession):
    """Sending image/gif (not in the accept list) → 415."""
    from app.database import get_tenant_db

    session, token_str, _tenant_id = await _build_http_test_artifacts(db)
    fake_storage = _make_fake_storage()

    async def _override_db():
        yield db

    app.dependency_overrides[get_tenant_db] = _override_db
    try:
        with _patch_bypass_session_to(db), _patch_storage(fake_storage):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    f"/api/candidate-session/{token_str}/reference-photo",
                    files={"file": ("photo.gif", io.BytesIO(b"GIF89a"), "image/gif")},
                )
    finally:
        app.dependency_overrides.pop(get_tenant_db, None)

    assert response.status_code == 415, response.text
    fake_storage.upload_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_empty_file_returns_400(db: AsyncSession):
    """Sending zero bytes → 400."""
    from app.database import get_tenant_db

    session, token_str, _tenant_id = await _build_http_test_artifacts(db)
    fake_storage = _make_fake_storage()

    async def _override_db():
        yield db

    app.dependency_overrides[get_tenant_db] = _override_db
    try:
        with _patch_bypass_session_to(db), _patch_storage(fake_storage):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                response = await ac.post(
                    f"/api/candidate-session/{token_str}/reference-photo",
                    files={"file": ("empty.jpg", io.BytesIO(b""), "image/jpeg")},
                )
    finally:
        app.dependency_overrides.pop(get_tenant_db, None)

    assert response.status_code == 400, response.text
    fake_storage.upload_bytes.assert_not_awaited()
