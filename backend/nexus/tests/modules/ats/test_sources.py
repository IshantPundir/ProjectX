"""ATSImportSource.normalize: ATSApplicantPayload → SourcedCandidate."""
from __future__ import annotations

from datetime import datetime, timezone


def test_ats_import_source_normalizes_to_sourced_candidate():
    from app.modules.ats.sources import ATSImportSource
    from app.modules.ats.schemas import ATSApplicantPayload
    from app.modules.candidates.sources import SourcedCandidate

    payload = ATSApplicantPayload(
        external_id="appl-1", name="Jane Doe", email="jane@x.com",
        phone="555-0100", location="Bangalore",
        current_title="Sr Engineer", linkedin_url="https://linkedin.com/in/jane",
        notes=None,
        raw={"id": "appl-1", "extra_vendor_field": "preserved"},
        fetched_at=datetime.now(tz=timezone.utc),
    )
    src = ATSImportSource(vendor="ceipal")
    out = src.normalize(payload)

    assert isinstance(out, SourcedCandidate)
    assert out.name == "Jane Doe"
    assert out.email == "jane@x.com"
    assert out.source == "ats_ceipal"
    assert out.external_id == "appl-1"
    assert out.source_metadata["extra_vendor_field"] == "preserved"


def test_vendor_prefix_is_applied():
    from app.modules.ats.sources import ATSImportSource
    from app.modules.ats.schemas import ATSApplicantPayload
    from datetime import datetime, timezone

    payload = ATSApplicantPayload(
        external_id="g-1", name="X", email="x@y.com",
        raw={}, fetched_at=datetime.now(tz=timezone.utc),
    )
    assert ATSImportSource("ceipal").normalize(payload).source == "ats_ceipal"
    assert ATSImportSource("greenhouse").normalize(payload).source == "ats_greenhouse"
