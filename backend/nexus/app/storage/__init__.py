"""Object storage — provider-agnostic public API.

Import the factory and protocol from here; never import the concrete
implementation across module boundaries.

    from app.storage import get_object_storage, ObjectStorage

Switching storage providers (R2 ↔ AWS S3 ↔ Supabase Storage ↔ MinIO) is a
config change only — see `Settings.recording_storage_*` in app/config.py.
"""
from __future__ import annotations

from app.config import settings
from app.storage.base import ObjectMeta, ObjectStorage
from app.storage.s3 import S3CompatibleStorage


def get_object_storage() -> ObjectStorage:
    """Construct the configured object-storage client.

    Single swap point. All currently supported providers are S3-compatible,
    so this always returns an `S3CompatibleStorage`; a future non-S3 provider
    would branch here on a new `recording_storage_provider` setting.
    """
    return S3CompatibleStorage(
        bucket=settings.recording_storage_bucket,
        region=settings.recording_storage_region,
        endpoint_url=settings.recording_storage_endpoint_url,
        access_key_id=settings.recording_storage_access_key_id,
        secret_access_key=settings.recording_storage_secret_access_key,
        force_path_style=settings.recording_storage_force_path_style,
    )


__all__ = [
    "ObjectMeta",
    "ObjectStorage",
    "S3CompatibleStorage",
    "get_object_storage",
]
