"""Sensitive-PII stripping at adapter boundary + orchestrator persistence.

`strip_sensitive_pii` is the second-layer defence after the Ceipal adapter
strips known offenders at the wire. Verifies both positive (must-strip) and
negative (must-preserve) cases.
"""
from __future__ import annotations

import pytest

from app.modules.candidates.pii import strip_sensitive_pii


def test_strip_drops_aadhar_at_top_level():
    out = strip_sensitive_pii({"aadhar_number": "1234-5678-9012", "name": "Asha"})
    assert "aadhar_number" not in out
    assert out["name"] == "Asha"


def test_strip_drops_aadhaar_variant_spelling():
    out = strip_sensitive_pii({"aadhaar": "x", "name": "Asha"})
    assert "aadhaar" not in out
    assert out["name"] == "Asha"


def test_strip_drops_ssn_pan_passport_drivers_license_tax_id_nric_emirates():
    payload = {
        "ssn": "111-22-3333",
        "pan_number": "ABCDE1234F",
        "passport_number": "P12345",
        "drivers_license": "DL-X",
        "tax_id": "T-1",
        "nric": "S-1234",
        "emirates_id": "E-1",
        "kept_field": "ok",
    }
    out = strip_sensitive_pii(payload)
    for k in ("ssn", "pan_number", "passport_number", "drivers_license",
              "tax_id", "nric", "emirates_id"):
        assert k not in out, f"{k} should be stripped"
    assert out["kept_field"] == "ok"


def test_strip_drops_any_field_ending_with_token():
    payload = {
        "resume_token": "abcd",
        "auth_token": "xyz",
        "merge_document_path": "/tmp/p",
        "merged_pdf_document": "PDFDATA",
        "non_token_field": "keep",
    }
    out = strip_sensitive_pii(payload)
    assert "resume_token" not in out
    assert "auth_token" not in out
    assert "merge_document_path" not in out
    assert "merged_pdf_document" not in out
    assert out["non_token_field"] == "keep"


def test_strip_descends_into_nested_dicts():
    payload = {
        "applicant": {
            "id": "x",
            "aadhar_number": "1111",
            "profile": {"resume_token": "yyy", "name": "Asha"},
        },
        "documents": [
            {"name": "resume.pdf", "resume_token": "z1"},
            {"name": "cv.pdf", "resume_token": "z2"},
        ],
    }
    out = strip_sensitive_pii(payload)
    assert "aadhar_number" not in out["applicant"]
    assert "resume_token" not in out["applicant"]["profile"]
    assert out["applicant"]["profile"]["name"] == "Asha"
    for doc in out["documents"]:
        assert "resume_token" not in doc
        assert "name" in doc


def test_strip_does_not_mutate_input():
    payload = {"aadhar_number": "1111", "name": "Asha"}
    _ = strip_sensitive_pii(payload)
    assert payload["aadhar_number"] == "1111"


def test_strip_preserves_unrelated_keys_verbatim():
    payload = {
        "email": "a@b.com",
        "phone": "+9112345",
        "city": "Bengaluru",
        "skills": ["python", "react"],
    }
    assert strip_sensitive_pii(payload) == payload


def test_strip_is_case_insensitive():
    payload = {"Aadhar_Number": "x", "RESUME_TOKEN": "y", "Ssn": "z"}
    out = strip_sensitive_pii(payload)
    assert out == {}


def test_strip_raises_typeerror_on_non_dict():
    with pytest.raises(TypeError):
        strip_sensitive_pii("not a dict")  # type: ignore[arg-type]


def test_strip_handles_empty_dict():
    assert strip_sensitive_pii({}) == {}
