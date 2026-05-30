"""Provider-agnostic object-storage contract.

The interview-recording feature stores one MP4 per session in an
S3-compatible object store. The concrete provider (Cloudflare R2, AWS S3,
Supabase Storage, MinIO, …) is a deployment choice expressed entirely in
config — business logic depends only on this `ObjectStorage` protocol, never
on a vendor SDK surface. This mirrors the provider-agnostic discipline used
for auth (`app/modules/auth`) and AI (`app/ai`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ObjectMeta:
    """Metadata for a stored object (the subset the recording flow needs)."""

    key: str
    size_bytes: int
    content_type: str | None


class ObjectStorage(Protocol):
    """The minimal object-storage surface the recording flow depends on.

    Intentionally tiny: the backend never streams object bytes itself.
    Egress writes the object directly to the bucket; the backend only needs
    to (a) confirm an object exists and (b) mint a short-lived presigned GET
    URL the recruiter's browser streams from.
    """

    async def presign_get_url(self, key: str, *, ttl_seconds: int) -> str:
        """Return a time-limited presigned URL for streaming the object.

        The URL is safe to hand to a browser `<video>` element; the bucket
        itself stays private. Generation is a local signing operation (no
        network round-trip).
        """
        ...

    async def head(self, key: str) -> ObjectMeta | None:
        """Return object metadata, or None if the object does not exist."""
        ...

    async def download_to_path(self, key: str, dest_path: str) -> None:
        """Download the object to a local filesystem path (overwrites)."""
        ...
