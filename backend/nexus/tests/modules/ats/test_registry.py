"""Registry returns the right adapter class by vendor; raises on unknown."""
from __future__ import annotations

import uuid

import pytest

from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.errors import ATSUnknownVendorError


def _state(vendor: str) -> ATSConnectionState:
    return ATSConnectionState(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), vendor=vendor,
        credentials={},
    )


def test_get_ats_adapter_returns_ceipal_for_ceipal_vendor():
    from app.modules.ats.registry import get_ats_adapter
    from app.modules.ats.adapters.ceipal import CeipalAdapter

    adapter = get_ats_adapter(_state("ceipal"))
    assert isinstance(adapter, CeipalAdapter)
    assert adapter.state.vendor == "ceipal"


def test_get_ats_adapter_raises_on_unknown_vendor():
    from app.modules.ats.registry import get_ats_adapter

    with pytest.raises(ATSUnknownVendorError) as exc_info:
        get_ats_adapter(_state("greenhouse_v2_alpha"))
    assert "greenhouse_v2_alpha" in str(exc_info.value)


def test_supported_vendors_includes_ceipal():
    from app.modules.ats.registry import SUPPORTED_VENDORS
    assert "ceipal" in SUPPORTED_VENDORS
