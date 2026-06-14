"""tenant_settings module public API.

Cross-module callers MUST import from this package:
    from app.modules.tenant_settings import (
        TenantSettings, get_tenant_settings, DEFAULT_TENANT_SETTINGS,
    )

Deep imports (`from app.modules.tenant_settings.service import ...`) are
forbidden by `tests/test_module_boundaries.py`.
"""
from __future__ import annotations

from app.modules.tenant_settings.schemas import TenantSettings
from app.modules.tenant_settings.service import (
    DEFAULT_TENANT_SETTINGS,
    get_tenant_settings,
)

__all__ = [
    "DEFAULT_TENANT_SETTINGS",
    "TenantSettings",
    "get_tenant_settings",
]
