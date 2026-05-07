"""EngineCheckpoint — full in-memory state snapshot for crash recovery."""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.interview_engine.models.claims import ClaimsPoolSnapshot
from app.modules.interview_engine.models.ledger import SignalLedgerSnapshot
from app.modules.interview_engine.models.queue import QuestionQueueSnapshot
from app.modules.interview_engine.state.lifecycle import LifecycleSnapshot


class EngineCheckpoint(BaseModel):
    """Full in-memory engine state for crash recovery and forensic inspection.

    Stored in sessions.engine_checkpoint JSONB. Written every N turns or N seconds
    (whichever first) per ENGINE_CHECKPOINT_TURNS / ENGINE_CHECKPOINT_SECONDS.
    """

    schema_version: int = Field(default=1, ge=1)
    session_id: str
    ledger: SignalLedgerSnapshot
    queue: QuestionQueueSnapshot
    claims: ClaimsPoolSnapshot
    lifecycle: LifecycleSnapshot
    last_audit_seq_flushed: int = Field(ge=0)
    captured_at_ms: int = Field(ge=0)
