"""Resume upload orchestration — S3 interactions mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.models import Candidate
from app.modules.auth.context import UserContext
from app.modules.candidates.errors import (
    InvalidResumeContentTypeError,
    ResumeNotFoundInS3Error,
)
from tests.conftest import create_test_client, create_test_user


def _make_ctx(user):
    return UserContext(user=user, is_super_admin=False, assignments=[])


async def _make_candidate(db, tenant_id, created_by):
    c = Candidate(
        tenant_id=tenant_id,
        name="Alice",
        email="alice@example.com",
        source="manual",
        created_by=created_by,
    )
    db.add(c)
    await db.flush()
    return c


@pytest.mark.asyncio
async def test_request_resume_upload_returns_presigned_url(db):
    from app.modules.candidates.resume_service import request_resume_upload

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    candidate = await _make_candidate(db, tenant.id, user.id)

    fake_client = MagicMock()
    fake_client.generate_presigned_url.return_value = "https://s3.example.com/signed"
    with patch(
        "app.modules.candidates.resume_service._s3_client",
        return_value=fake_client,
    ):
        response = await request_resume_upload(db, candidate.id, _make_ctx(user))

    assert response.upload_url == "https://s3.example.com/signed"
    # Key includes the candidate's hex id
    assert candidate.id.hex in response.s3_key
    assert response.expires_in_seconds > 0
    fake_client.generate_presigned_url.assert_called_once()


@pytest.mark.asyncio
async def test_confirm_resume_upload_persists_on_valid_pdf(db):
    from app.modules.candidates.resume_service import confirm_resume_upload

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    candidate = await _make_candidate(db, tenant.id, user.id)

    fake_client = MagicMock()
    fake_client.head_object.return_value = {"ContentType": "application/pdf"}
    with patch(
        "app.modules.candidates.resume_service._s3_client",
        return_value=fake_client,
    ):
        await confirm_resume_upload(db, candidate.id, "some-key", _make_ctx(user))

    await db.refresh(candidate)
    assert candidate.resume_s3_key == "some-key"
    assert candidate.resume_uploaded_at is not None


@pytest.mark.asyncio
async def test_confirm_resume_upload_rejects_missing_object(db):
    from app.modules.candidates.resume_service import confirm_resume_upload

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    candidate = await _make_candidate(db, tenant.id, user.id)

    fake_client = MagicMock()
    fake_client.head_object.side_effect = ClientError(
        {"Error": {"Code": "404"}}, "HeadObject"
    )
    with patch(
        "app.modules.candidates.resume_service._s3_client",
        return_value=fake_client,
    ):
        with pytest.raises(ResumeNotFoundInS3Error):
            await confirm_resume_upload(db, candidate.id, "ghost-key", _make_ctx(user))


@pytest.mark.asyncio
async def test_confirm_resume_upload_rejects_non_pdf(db):
    from app.modules.candidates.resume_service import confirm_resume_upload

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    candidate = await _make_candidate(db, tenant.id, user.id)

    fake_client = MagicMock()
    fake_client.head_object.return_value = {"ContentType": "image/jpeg"}
    with patch(
        "app.modules.candidates.resume_service._s3_client",
        return_value=fake_client,
    ):
        with pytest.raises(InvalidResumeContentTypeError):
            await confirm_resume_upload(db, candidate.id, "jpeg-key", _make_ctx(user))


@pytest.mark.asyncio
async def test_delete_resume_clears_columns_and_deletes_object(db):
    from datetime import UTC, datetime

    from app.modules.candidates.resume_service import delete_resume

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    candidate = await _make_candidate(db, tenant.id, user.id)
    candidate.resume_s3_key = "existing-key"
    candidate.resume_uploaded_at = datetime.now(UTC)
    await db.flush()

    fake_client = MagicMock()
    with patch(
        "app.modules.candidates.resume_service._s3_client",
        return_value=fake_client,
    ):
        await delete_resume(db, candidate.id, _make_ctx(user))

    fake_client.delete_object.assert_called_once()
    assert candidate.resume_s3_key is None
    assert candidate.resume_uploaded_at is None


@pytest.mark.asyncio
async def test_delete_resume_is_idempotent_when_no_existing_key(db):
    from app.modules.candidates.resume_service import delete_resume

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    candidate = await _make_candidate(db, tenant.id, user.id)
    # No resume_s3_key set — delete should short-circuit on S3 but still clear/flush.

    fake_client = MagicMock()
    with patch(
        "app.modules.candidates.resume_service._s3_client",
        return_value=fake_client,
    ):
        await delete_resume(db, candidate.id, _make_ctx(user))

    fake_client.delete_object.assert_not_called()
    assert candidate.resume_s3_key is None


@pytest.mark.asyncio
async def test_delete_resume_swallows_s3_client_error(db):
    """S3 delete failure (e.g. object already gone) must not break the column clear."""
    from app.modules.candidates.resume_service import delete_resume

    tenant = await create_test_client(db)
    user = await create_test_user(db, tenant.id)
    candidate = await _make_candidate(db, tenant.id, user.id)
    candidate.resume_s3_key = "maybe-gone-key"
    await db.flush()

    fake_client = MagicMock()
    fake_client.delete_object.side_effect = ClientError(
        {"Error": {"Code": "404"}}, "DeleteObject"
    )
    with patch(
        "app.modules.candidates.resume_service._s3_client",
        return_value=fake_client,
    ):
        await delete_resume(db, candidate.id, _make_ctx(user))

    assert candidate.resume_s3_key is None
