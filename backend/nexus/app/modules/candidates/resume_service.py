"""Resume upload orchestration — two-step flow.

1. request_resume_upload() — mint a pre-signed PUT URL pointing at a known
   per-candidate S3 key. Backend touches no file bytes. Frontend uploads
   directly to S3, under the bucket policy that only accepts PDFs of the
   expected size.
2. confirm_resume_upload(s3_key) — frontend reports success. Backend HEADs
   the object: must exist AND content-type must be `application/pdf`. On
   success, commit `resume_s3_key` + `resume_uploaded_at` to the candidate
   row, writing a `candidate.resume_uploaded` audit event.
3. delete_resume() — idempotent delete of the S3 object and clearing of the
   column pair. Logs `candidate.resume_deleted` regardless of whether an
   S3 object actually existed (column clear is the source of truth for UI).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import boto3
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.audit.service import log_event
from app.modules.auth.context import UserContext
from app.modules.candidates.errors import (
    InvalidResumeContentTypeError,
    ResumeNotFoundInS3Error,
)
from app.modules.candidates.schemas import ResumeUploadUrlResponse
from app.modules.candidates.service import get_candidate


def _s3_client():
    """Create a fresh S3 client. Overridden via patch in tests."""
    return boto3.client("s3", region_name=settings.aws_region)


def _resume_key(candidate_id: UUID) -> str:
    return f"candidate-resumes/{candidate_id.hex}/resume.pdf"


async def request_resume_upload(
    db: AsyncSession, candidate_id: UUID, user: UserContext
) -> ResumeUploadUrlResponse:
    """Return a pre-signed PUT URL for the candidate's resume slot.

    Raises CandidateNotFoundError if the candidate doesn't exist.
    Note: pre-signed URL generation is local (no network call), so we call
    boto3 synchronously.
    """
    await get_candidate(db, candidate_id)
    s3_key = _resume_key(candidate_id)
    url = _s3_client().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.aws_s3_bucket_candidate_resumes,
            "Key": s3_key,
            "ContentType": "application/pdf",
        },
        ExpiresIn=settings.resume_upload_url_ttl_seconds,
    )
    return ResumeUploadUrlResponse(
        upload_url=url,
        s3_key=s3_key,
        expires_in_seconds=settings.resume_upload_url_ttl_seconds,
    )


async def confirm_resume_upload(
    db: AsyncSession, candidate_id: UUID, s3_key: str, user: UserContext
) -> None:
    """HEAD the uploaded object; require PDF content-type; persist resume_s3_key.

    Raises:
        CandidateNotFoundError: candidate doesn't exist.
        ResumeNotFoundInS3Error: HEAD returned 404 (or any ClientError).
        InvalidResumeContentTypeError: object exists but isn't PDF.
    """
    candidate = await get_candidate(db, candidate_id)

    client = _s3_client()
    try:
        head = await asyncio.to_thread(
            client.head_object,
            Bucket=settings.aws_s3_bucket_candidate_resumes,
            Key=s3_key,
        )
    except ClientError as e:
        raise ResumeNotFoundInS3Error() from e

    content_type = head.get("ContentType", "")
    if content_type != "application/pdf":
        raise InvalidResumeContentTypeError()

    candidate.resume_s3_key = s3_key
    candidate.resume_uploaded_at = datetime.now(UTC)
    await db.flush()

    await log_event(
        db,
        tenant_id=candidate.tenant_id,
        actor_id=user.user.id,
        actor_email=user.user.email,
        action="candidate.resume_uploaded",
        resource="candidate",
        resource_id=candidate.id,
        payload={"s3_key": s3_key},
    )


async def delete_resume(
    db: AsyncSession, candidate_id: UUID, user: UserContext
) -> None:
    """Idempotent delete: drop the S3 object (if any) and clear the columns."""
    candidate = await get_candidate(db, candidate_id)
    existing_key = candidate.resume_s3_key
    if existing_key:
        client = _s3_client()
        try:
            await asyncio.to_thread(
                client.delete_object,
                Bucket=settings.aws_s3_bucket_candidate_resumes,
                Key=existing_key,
            )
        except ClientError:
            pass  # Idempotent — object may have been purged already.

    candidate.resume_s3_key = None
    candidate.resume_uploaded_at = None
    await db.flush()

    await log_event(
        db,
        tenant_id=candidate.tenant_id,
        actor_id=user.user.id,
        actor_email=user.user.email,
        action="candidate.resume_deleted",
        resource="candidate",
        resource_id=candidate.id,
        payload={"s3_key": existing_key} if existing_key else {},
    )
