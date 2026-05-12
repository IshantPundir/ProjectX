"""Vendor-keyed factory for ATSAdapter instances.

Adding a new vendor:
  1. Implement app/modules/ats/adapters/<vendor>.py satisfying ATSAdapter.
  2. Add `<VendorAdapter>.vendor: <VendorAdapter>` to _REGISTRY.
  3. Define the vendor's credential schema in the connection-create router.

Vendor selection is per-CONNECTION (data — state.vendor), not per-deployment
(env). Different tenants can use different ATSes simultaneously.
"""
from __future__ import annotations

from typing import Type

from app.modules.ats.adapter import ATSAdapter
from app.modules.ats.adapters.ceipal import CeipalAdapter
from app.modules.ats.connection import ATSConnectionState
from app.modules.ats.errors import ATSUnknownVendorError


_REGISTRY: dict[str, Type[ATSAdapter]] = {
    CeipalAdapter.vendor: CeipalAdapter,        # type: ignore[type-abstract]
    # GreenhouseAdapter.vendor: GreenhouseAdapter,    # future
    # WorkdayAdapter.vendor: WorkdayAdapter,          # future
}

SUPPORTED_VENDORS: frozenset[str] = frozenset(_REGISTRY.keys())


def get_ats_adapter(state: ATSConnectionState) -> ATSAdapter:
    """Construct the adapter for `state.vendor`.

    Raises ATSUnknownVendorError if the vendor is not registered — indicates
    either a config drift (vendor was deregistered) or a malformed DB row;
    either case requires engineering investigation, so it's permanent.
    """
    cls = _REGISTRY.get(state.vendor)
    if cls is None:
        raise ATSUnknownVendorError(
            f"No ATS adapter registered for vendor {state.vendor!r}. "
            f"Supported: {sorted(SUPPORTED_VENDORS)}"
        )
    return cls(state)  # type: ignore[call-arg]
