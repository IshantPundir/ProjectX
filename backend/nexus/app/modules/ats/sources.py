"""Bridge between canonical ATS DTOs and the CandidateSource Protocol.

Lives in ats/, not in candidates/, to keep the cross-module import direction
acyclic (ats imports from candidates; candidates does NOT import from ats).
The orchestrator calls candidates.service.import_candidate(sourced=...) with
the SourcedCandidate produced here.
"""
from __future__ import annotations

from app.modules.ats.schemas import ATSApplicantPayload
from app.modules.candidates.sources import SourcedCandidate


class ATSImportSource:
    """Normalizes ATSApplicantPayload → SourcedCandidate.

    Vendor-parameterised because the resulting candidate.source string is
    tagged with the vendor ('ats_ceipal' / 'ats_greenhouse' / …).
    """

    def __init__(self, vendor: str) -> None:
        self._vendor = vendor

    def normalize(self, raw: ATSApplicantPayload) -> SourcedCandidate:
        return SourcedCandidate(
            name=raw.name,
            email=raw.email,
            phone=raw.phone,
            location=raw.location,
            current_title=raw.current_title,
            linkedin_url=raw.linkedin_url,
            notes=raw.notes,
            source=f"ats_{self._vendor}",
            external_id=raw.external_id,
            source_metadata=raw.raw,
        )
