"""Sensitive-PII stripping for vendor payloads before persistence.

`strip_sensitive_pii` is applied at every adapter boundary that yields
applicant data (currently `ATSApplicantPayload.raw` and
`ATSSubmissionPayload.raw`). It's a defence-in-depth helper: the Ceipal
adapter already strips the known offenders at the wire boundary; this
function catches anything the adapter forgot, including future vendor
additions that match the canonical sensitive-key patterns.

Fields stripped (case-insensitive, any depth):
  - National IDs: aadhar, ssn, sin, pan_number, passport, drivers_license,
    tax_id, nric, emirates_id
  - Resume artifacts: resume_token, merged_pdf_document, merge_document_path
  - Any field whose name ends with ``_token`` (catches future auth artifacts)
"""
from __future__ import annotations

import copy
import re
from typing import Any

_SENSITIVE_KEY_PATTERNS = re.compile(
    r"^("
    r"(aadhar|aadhaar)(_number)?"   # aadhar | aadhaar | aadhar_number | aadhaar_number
    r"|ssn|sin"
    r"|pan_number"
    r"|passport(_number)?"
    r"|drivers_license"
    r"|tax_id"
    r"|nric"
    r"|emirates_id"
    r"|resume_token"
    r"|merge_document_path"
    r"|merged_pdf_document"
    r"|.+_token"                      # any field ending in _token (>=1 char prefix)
    r")$",
    re.IGNORECASE,
)


def _is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_PATTERNS.match(key))


def _strip_in_place(value: Any) -> Any:
    """Walk dict/list structures, removing sensitive keys. Returns `value`."""
    if isinstance(value, dict):
        for key in list(value.keys()):
            if _is_sensitive_key(key):
                value.pop(key)
            else:
                _strip_in_place(value[key])
    elif isinstance(value, list):
        for item in value:
            _strip_in_place(item)
    return value


def strip_sensitive_pii(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of `payload` with sensitive keys removed at any depth.

    The original `payload` is never mutated. Non-dict/non-list values are
    preserved verbatim (we strip keys, not values).
    """
    if not isinstance(payload, dict):
        raise TypeError("strip_sensitive_pii requires a dict payload")
    sanitized: dict[str, Any] = copy.deepcopy(payload)
    _strip_in_place(sanitized)
    return sanitized
