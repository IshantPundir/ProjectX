"""Org-units module — tenant + hierarchical unit modeling.

Public surface for cross-module callers.
"""
from app.modules.org_units.models import Client, OrganizationalUnit
from app.modules.org_units.service import (
    create_org_unit,
    find_company_profile_in_ancestry,
    get_org_unit_ancestry,
    nullify_deletable_by_for_user,
)

__all__ = [
    "Client",
    "OrganizationalUnit",
    "create_org_unit",
    "find_company_profile_in_ancestry",
    "get_org_unit_ancestry",
    "nullify_deletable_by_for_user",
]
