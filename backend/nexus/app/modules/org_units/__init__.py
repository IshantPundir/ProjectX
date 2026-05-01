"""Org-units module — tenant + hierarchical unit modeling.

Public surface for cross-module callers.

NOTE: service-function exports (``create_org_unit``,
``find_company_profile_in_ancestry``, ``get_org_unit_ancestry``,
``nullify_deletable_by_for_user``) are DEFERRED to Stage E.2
(sub-commit 4d-2). They cannot be eagerly imported here while the
``app/models.py`` shim is still in place — see auth/__init__.py for
the cycle explanation. Removing the shim in 4d-2 lets us add them.
"""
from app.modules.org_units.models import Client, OrganizationalUnit

__all__ = [
    "Client",
    "OrganizationalUnit",
]
