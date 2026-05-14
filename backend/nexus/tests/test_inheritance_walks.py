"""Tests for the locale + compliance ancestry walks.

These functions (`find_locale_defaults_in_ancestry`,
`find_compliance_flags_in_ancestry`) were deleted as part of the
company-profile column refactor (migration 0034). The locale/compliance
system was replaced by a per-field country/state/city address walk.

The equivalent coverage now lives in:
  tests/modules/org_units/test_ancestry.py — test_find_address_in_ancestry_*
"""

import pytest


@pytest.mark.skip(
    reason=(
        "find_locale_defaults_in_ancestry and find_compliance_flags_in_ancestry "
        "were deleted in the 0034 company-profile column refactor. "
        "Equivalent coverage lives in tests/modules/org_units/test_ancestry.py."
    )
)
def test_locale_returns_own_values_for_source_unit():
    pass


@pytest.mark.skip(
    reason=(
        "find_locale_defaults_in_ancestry and find_compliance_flags_in_ancestry "
        "were deleted in the 0034 company-profile column refactor. "
        "Equivalent coverage lives in tests/modules/org_units/test_ancestry.py."
    )
)
def test_locale_inherits_from_company_through_division_to_team():
    pass


@pytest.mark.skip(
    reason=(
        "find_locale_defaults_in_ancestry and find_compliance_flags_in_ancestry "
        "were deleted in the 0034 company-profile column refactor. "
        "Equivalent coverage lives in tests/modules/org_units/test_ancestry.py."
    )
)
def test_locale_partial_override_uses_closest_ancestor_per_key():
    pass


@pytest.mark.skip(
    reason=(
        "find_locale_defaults_in_ancestry and find_compliance_flags_in_ancestry "
        "were deleted in the 0034 company-profile column refactor. "
        "Equivalent coverage lives in tests/modules/org_units/test_ancestry.py."
    )
)
def test_locale_returns_none_when_unset_anywhere():
    pass


@pytest.mark.skip(
    reason=(
        "find_locale_defaults_in_ancestry and find_compliance_flags_in_ancestry "
        "were deleted in the 0034 company-profile column refactor. "
        "Equivalent coverage lives in tests/modules/org_units/test_ancestry.py."
    )
)
def test_compliance_treats_false_as_set_value():
    pass


@pytest.mark.skip(
    reason=(
        "find_locale_defaults_in_ancestry and find_compliance_flags_in_ancestry "
        "were deleted in the 0034 company-profile column refactor. "
        "Equivalent coverage lives in tests/modules/org_units/test_ancestry.py."
    )
)
def test_compliance_returns_none_when_no_flag_anywhere():
    pass
