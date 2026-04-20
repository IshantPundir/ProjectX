"""Candidate source abstraction.

A CandidateSource converts provider-specific input into a normalized
SourcedCandidate ready for insertion. ManualSource (recruiter typing into the
Add Candidate form) is the only adapter in Phase 3B. CsvBulkSource and the
ATS adapters (Ceipal, Greenhouse, Workday) plug in under the same Protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.modules.candidates.schemas import CandidateCreateRequest


@dataclass(frozen=True, slots=True)
class SourcedCandidate:
    """Normalized candidate shape every source produces.

    Ready to hand to the service layer for insertion into `candidates` — the
    service owns tenant_id stamping, created_by assignment, and row commit.
    """

    name: str
    email: str
    phone: str | None
    location: str | None
    current_title: str | None
    linkedin_url: str | None
    notes: str | None
    source: str
    external_id: str | None
    source_metadata: dict | None


class CandidateSource(Protocol):
    """Every source adapter implements this."""

    def normalize(self, raw: object) -> SourcedCandidate: ...


class ManualSource:
    """Recruiter typing into the Add Candidate form."""

    def normalize(self, raw: CandidateCreateRequest) -> SourcedCandidate:
        return SourcedCandidate(
            name=raw.name,
            email=raw.email,
            phone=raw.phone,
            location=raw.location,
            current_title=raw.current_title,
            linkedin_url=str(raw.linkedin_url) if raw.linkedin_url else None,
            notes=raw.notes,
            source=raw.source.value,
            external_id=raw.external_id,
            source_metadata=raw.source_metadata,
        )
