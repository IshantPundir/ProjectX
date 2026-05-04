"""LedgerPersistence — async fire-and-forget Redis writeback.

Maintains a Redis side-copy of `InterviewState` + `SignalLedger` so a
fresh agent process after a candidate reconnect can rehydrate without
re-reading the entire audit envelope.

Persistence model:

* **Best-effort writes.** Redis is a safety net, not the source of
  truth. Every public mutation method returns ``True`` on a successful
  write, ``False`` if Redis was unreachable / errored / timed out.
  The methods do NOT raise — a Redis outage must not break a live
  interview turn. Failures are logged at warning level.
* **Fire-and-forget.** Writes are wrapped in ``asyncio.shield`` so a
  cancelled caller doesn't tear down a half-sent write.
* **Gap detection.** Each successful write stamps the source object's
  ``sequence_number`` into ``self._last_<state|ledger>_seq_persisted``.
  At session close, ``detect_gaps`` compares current in-memory seq vs
  last-persisted-seq. Any non-zero delta means writes were lost in
  flight; the result is logged into the audit envelope so the Report
  Builder can flag the session for review.
* **TTL** (default 6h) ensures abandoned sessions don't leak.

* **Gap-detection scope is the current process lifetime — v1
  limitation.** ``_last_state_seq_persisted`` and
  ``_last_ledger_seq_persisted`` are instance attributes, scoped to
  the LedgerPersistence object that lives in one ``StructuredInterviewAgent``
  process. On agent crash + fresh dispatch (or any cross-process
  rehydrate), the new instance starts with both attributes = ``None``;
  ``detect_gaps`` at the new agent's session-close will report "all
  of current_seq missing" because the new instance never wrote
  anything itself, even though the OLD process had successfully
  written most updates before crashing. This is acceptable for v1
  because crash-resume (the v2 capability that would make this a
  real bug) is explicitly out of scope per design doc §9.3 and
  §16. Cross-process gap tracking would require persisting the
  last-persisted-seq somewhere durable (Postgres or a Redis
  metadata key) and reading it on rehydrate — deferred to v2
  alongside hot-resume from Redis state. Until then, gap detection
  is a single-process correctness tool, not a crash-recovery audit.

Key layout (tenant-scoped — `nexus_app` role enforces no cross-tenant
key access at the Redis ACL level when that lands; today the prefix
is the only fence, sufficient because nothing else writes these keys):

```
tenant:{tenant_id}:session:{session_id}:state
tenant:{tenant_id}:session:{session_id}:ledger
```

The structured agent (Phase B) calls ``write_state`` on every phase
change and ``write_ledger`` on every ledger mutation. Both calls are
``await``-ed but neither blocks the turn — a slow Redis adds latency
proportional to its slowness, capped by ``socket_timeout`` on the
client; a hard failure fails-fast and logs.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from app.modules.interview_engine.orchestrator.ledger import SignalLedger
from app.modules.interview_engine.orchestrator.state import InterviewState

if TYPE_CHECKING:
    import redis.asyncio as aioredis  # noqa: TC004

logger = structlog.get_logger("interview-engine.orchestrator.persistence")

DEFAULT_TTL_SECONDS = 6 * 3600  # 6 hours


def _state_key(tenant_id: str, session_id: str) -> str:
    return f"tenant:{tenant_id}:session:{session_id}:state"


def _ledger_key(tenant_id: str, session_id: str) -> str:
    return f"tenant:{tenant_id}:session:{session_id}:ledger"


class LedgerPersistence:
    """Tenant-scoped Redis writeback for one in-flight session."""

    def __init__(
        self,
        client: aioredis.Redis,
        *,
        tenant_id: str,
        session_id: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._client = client
        self._tenant_id = tenant_id
        self._session_id = session_id
        self._ttl = ttl_seconds
        self._last_state_seq_persisted: int | None = None
        self._last_ledger_seq_persisted: int | None = None

    # ------------------------------------------------------------------
    # Writes — best-effort, never raise.
    # ------------------------------------------------------------------

    async def write_state(self, state: InterviewState) -> bool:
        """Persist InterviewState. Returns True on success, False on failure.

        The caller's tenant/session must match the persistence
        instance's; mismatches are a programmer error and raise.
        """
        self._require_match(
            tenant_id=state.tenant_id, session_id=state.session_id, kind="state"
        )
        body = state.model_dump_json()
        ok = await self._set_with_ttl(
            _state_key(self._tenant_id, self._session_id), body, kind="state",
        )
        if ok:
            self._last_state_seq_persisted = state.sequence_number
        return ok

    async def write_ledger(self, ledger: SignalLedger) -> bool:
        """Persist SignalLedger. Returns True on success, False on failure."""
        body = ledger.model_dump_json()
        ok = await self._set_with_ttl(
            _ledger_key(self._tenant_id, self._session_id),
            body,
            kind="ledger",
        )
        if ok:
            self._last_ledger_seq_persisted = ledger.sequence_number
        return ok

    # ------------------------------------------------------------------
    # Reads — returns None on miss / failure.
    # ------------------------------------------------------------------

    async def load_state(self) -> InterviewState | None:
        """Rehydrate InterviewState from Redis. None on miss or failure."""
        body = await self._get(
            _state_key(self._tenant_id, self._session_id), kind="state",
        )
        if body is None:
            return None
        try:
            return InterviewState.model_validate_json(body)
        except Exception as exc:  # noqa: BLE001 - validation can fail on schema drift
            logger.warning(
                "persistence.load_state.parse_failed",
                tenant_id=self._tenant_id,
                session_id=self._session_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    async def load_ledger(self) -> SignalLedger | None:
        """Rehydrate SignalLedger from Redis. None on miss or failure."""
        body = await self._get(
            _ledger_key(self._tenant_id, self._session_id), kind="ledger",
        )
        if body is None:
            return None
        try:
            return SignalLedger.model_validate_json(body)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "persistence.load_ledger.parse_failed",
                tenant_id=self._tenant_id,
                session_id=self._session_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    # ------------------------------------------------------------------
    # Gap detection — called at session close.
    # ------------------------------------------------------------------

    def detect_gaps(
        self,
        *,
        current_state_seq: int,
        current_ledger_seq: int,
    ) -> dict[str, int]:
        """Compare in-memory sequence numbers to last-persisted ones.

        Returns ``{"state_gap": N, "ledger_gap": M}`` where each value
        is the count of mutations that did NOT make it to Redis (zero
        means everything persisted). Logged into the audit envelope at
        session close so the Report Builder can flag suspect sessions.

        ``None`` last-persisted means "nothing ever wrote successfully";
        the gap is the entire current sequence in that case.
        """
        state_gap = current_state_seq - (self._last_state_seq_persisted or 0)
        ledger_gap = current_ledger_seq - (self._last_ledger_seq_persisted or 0)
        if state_gap > 0 or ledger_gap > 0:
            logger.warning(
                "persistence.detect_gaps.gaps_present",
                tenant_id=self._tenant_id,
                session_id=self._session_id,
                state_gap=state_gap,
                ledger_gap=ledger_gap,
                last_state_seq_persisted=self._last_state_seq_persisted,
                last_ledger_seq_persisted=self._last_ledger_seq_persisted,
                current_state_seq=current_state_seq,
                current_ledger_seq=current_ledger_seq,
            )
        return {"state_gap": state_gap, "ledger_gap": ledger_gap}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_match(
        self, *, tenant_id: str, session_id: str, kind: str
    ) -> None:
        if tenant_id != self._tenant_id or session_id != self._session_id:
            raise ValueError(
                f"LedgerPersistence({self._tenant_id}/{self._session_id}) cannot "
                f"persist {kind} for tenant_id={tenant_id} session_id={session_id}"
            )

    async def _set_with_ttl(self, key: str, body: str, *, kind: str) -> bool:
        """Best-effort SET with TTL; True on success, False on failure."""
        try:
            await asyncio.shield(
                self._client.set(key, body, ex=self._ttl)
            )
            logger.info(
                "persistence.write.ok",
                kind=kind,
                tenant_id=self._tenant_id,
                session_id=self._session_id,
                bytes=len(body),
            )
            return True
        except Exception as exc:  # noqa: BLE001 - best-effort
            logger.warning(
                "persistence.write.failed",
                kind=kind,
                tenant_id=self._tenant_id,
                session_id=self._session_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False

    async def _get(self, key: str, *, kind: str) -> str | None:
        """Best-effort GET; None on miss or failure."""
        try:
            raw = await self._client.get(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "persistence.read.failed",
                kind=kind,
                tenant_id=self._tenant_id,
                session_id=self._session_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        # `redis.asyncio.Redis.get` is typed `Any`; we've already covered
        # the None and bytes branches, so anything reaching here is a str.
        # `decode_responses=True` clients return str directly.
        assert isinstance(raw, str)
        return raw
