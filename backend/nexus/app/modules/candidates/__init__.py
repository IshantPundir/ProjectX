"""Candidates module — candidate identity, assignments, kanban."""
from app.modules.candidates.errors import CandidateNotFoundError
from app.modules.candidates.models import (
    Candidate,
    CandidateJobAssignment,
    CandidateStageProgress,
)

__all__ = [
    "Candidate",
    "CandidateJobAssignment",
    "CandidateNotFoundError",
    "CandidateStageProgress",
]
