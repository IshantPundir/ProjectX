"""ATS vendor canonicalization constants.

Every ATS-imported row carries a `source` string of the form `ats_<vendor>`.
Centralizing the prefix here means no other module concatenates the vendor
name by hand — `f"ats_{vendor}"` is forbidden outside this module.
"""
from __future__ import annotations

ATS_VENDOR_PREFIX = "ats_"

# Concrete vendors. Add new vendors here when their adapter ships.
ATS_VENDOR_CEIPAL = "ats_ceipal"


def is_ats_source(source: str) -> bool:
    """True iff `source` identifies an ATS-imported row."""
    return source.startswith(ATS_VENDOR_PREFIX)
