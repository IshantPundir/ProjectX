"""CandidateClaimsPool — capped append-only pool with drop-oldest semantics."""
from __future__ import annotations

from collections import deque

from app.modules.interview_engine.models.claims import ClaimEntry, ClaimsPoolSnapshot
from app.modules.interview_engine.models.judge import ClaimEntry as JudgeClaimEntry


class CandidateClaimsPool:
    def __init__(self, *, max_size: int) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._max_size = max_size
        self._entries: deque[ClaimEntry] = deque(maxlen=max_size)

    def add(
        self,
        judge_claim: JudgeClaimEntry,
        *,
        captured_at_turn: int,
        captured_at_seq: int,
    ) -> ClaimEntry:
        canonical = ClaimEntry(
            claim_topic=judge_claim.claim_topic,
            claim_text=judge_claim.claim_text,
            source_quote=judge_claim.source_quote,
            captured_at_turn=captured_at_turn,
            captured_at_seq=captured_at_seq,
        )
        self._entries.append(canonical)  # deque(maxlen) drops oldest automatically
        return canonical

    def snapshot(self) -> ClaimsPoolSnapshot:
        return ClaimsPoolSnapshot(entries=[e.model_copy() for e in self._entries])

    @classmethod
    def from_snapshot(cls, snap: ClaimsPoolSnapshot, *, max_size: int) -> "CandidateClaimsPool":
        pool = cls(max_size=max_size)
        for e in snap.entries:
            pool._entries.append(e.model_copy())
        return pool
