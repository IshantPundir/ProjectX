"""Committed-turn source — the drive loop's turn feed under native turn detection.

Path A+ replaces the gen-3 manual Ear poll loop with LiveKit native turn
detection (``turn_detection=MultilingualModel()``). The framework decides
end-of-turn from the live STT stream and fires ``on_user_turn_completed`` with
the FULL final transcript — so EOU and the transcript are produced together
(no commit/STT race, no one-turn lag). That hook submits the transcript here;
the ``SessionDriver`` drive loop consumes it.

This module is LiveKit-free: a thin ``asyncio.Queue`` wrapper with two
load-bearing behaviors.

1. **Empty / whitespace-only transcripts are dropped.** Empty STT finals
   (``eot_delay=0`` / ``conf=0`` in the F3 logs) must not produce spurious
   no-op turns. ``submit`` returns ``False`` when it drops.

2. **``close()`` unblocks a pending ``get()`` with a ``None`` sentinel** so the
   drive loop exits cleanly on session end (terminal directive or candidate
   disconnect) without a separate cancellation dance.
"""
from __future__ import annotations

import asyncio


class CommittedTurnSource:
    """FIFO feed of committed candidate turns for the drive loop.

    Construct one per session. The LiveKit ``on_user_turn_completed`` hook calls
    :meth:`submit`; the drive loop awaits :meth:`get`. Call :meth:`close` once
    at session end.
    """

    def __init__(self) -> None:
        # The None sentinel (pushed by close()) is the only non-str item.
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._closed: bool = False

    def submit(self, transcript: str | None) -> bool:
        """Offer a committed-turn transcript to the drive loop.

        Returns ``True`` if the transcript was enqueued, ``False`` if it was
        dropped (the source is closed, or the transcript is None / empty /
        whitespace-only).
        """
        if self._closed:
            return False
        if not transcript or not transcript.strip():
            return False
        self._queue.put_nowait(transcript)
        return True

    async def get(self) -> str | None:
        """Await the next committed turn.

        Returns the transcript string, or ``None`` once the source has been
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
