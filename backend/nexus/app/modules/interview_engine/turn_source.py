"""Committed-turn source — the drive loop's turn feed under native turn detection.

Path A+ replaces the gen-3 manual Ear poll loop with LiveKit native turn
detection (``turn_detection=MultilingualModel()``). The framework decides
end-of-turn from the live STT stream and fires ``on_user_turn_completed`` with
the assembled ``AssembledTurn`` — so EOU and the turn are produced together
(no commit/STT race, no one-turn lag). That hook submits the assembled turn
here; the ``SessionDriver`` drive loop consumes it.

This module is LiveKit-free: a thin ``asyncio.Queue`` wrapper with two
load-bearing behaviors.

1. **Empty / whitespace-only turns are dropped.** Empty STT finals
   (``eot_delay=0`` / ``conf=0`` in the F3 logs) must not produce spurious
   no-op turns. ``submit`` returns ``False`` when it drops.

2. **``close()`` unblocks a pending ``get()`` with a ``None`` sentinel** so the
   drive loop exits cleanly on session end (terminal directive or candidate
   disconnect) without a separate cancellation dance.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.modules.interview_runtime.evidence import TimeSpan
from app.modules.interview_runtime.models import WordTiming


@dataclass(frozen=True)
class AssembledTurn:
    """One logical candidate turn after fragment assembly — the unit the drive
    loop consumes. `text` is the merged answer; `span` covers all merged
    fragments; `suppress_bridge` is set on a merge-back re-flush (an ack already
    played); `is_reflush` is audit-only. `words` are the per-word STT timings of
    the merged answer, turn-relative (first word start = 0), concatenated across
    all merged fragments — empty when STT supplied no word timings."""
    text: str
    span: TimeSpan
    suppress_bridge: bool = False
    is_reflush: bool = False
    words: list[WordTiming] = field(default_factory=list)


class CommittedTurnSource:
    """FIFO feed of committed candidate turns for the drive loop.

    Construct one per session. The LiveKit ``on_user_turn_completed`` hook calls
    :meth:`submit`; the drive loop awaits :meth:`get`. Call :meth:`close` once
    at session end.
    """

    def __init__(self) -> None:
        # The None sentinel (pushed by close()) is the only non-AssembledTurn item.
        self._queue: asyncio.Queue[AssembledTurn | None] = asyncio.Queue()
        self._closed: bool = False

    def submit(self, turn: AssembledTurn | None) -> bool:
        """Offer an assembled turn to the drive loop. Returns False if the source
        is closed, or the turn is None / has empty-or-whitespace text."""
        if self._closed:
            return False
        if turn is None or not turn.text.strip():
            return False
        self._queue.put_nowait(turn)
        return True

    async def get(self) -> AssembledTurn | None:
        """Await the next committed turn.

        Returns the ``AssembledTurn``, or ``None`` once the source has been
        closed (and any still-queued real turns have been drained first).
        """
        return await self._queue.get()

    def close(self) -> None:
        """Close the source, unblocking a pending :meth:`get` with ``None``.

        Idempotent. Real turns already queued are delivered before the sentinel.
        """
        if self._closed:
            return
        self._closed = True
        self._queue.put_nowait(None)
