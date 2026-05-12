"""Candidates module — candidate identity, assignments, kanban."""
from app.modules.candidates.errors import CandidateNotFoundError
from app.modules.candidates.models import (
    Candidate,
    CandidateJobAssignment,
    CandidateStageProgress,
)
from app.modules.candidates.service import import_candidate
from app.modules.candidates.sources import SourcedCandidate

__all__ = [
    "Candidate",
    "CandidateJobAssignment",
    "CandidateNotFoundError",
    "CandidateStageProgress",
    "SourcedCandidate",
    "import_candidate",
]
