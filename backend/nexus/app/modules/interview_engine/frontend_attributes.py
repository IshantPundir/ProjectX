"""Frontend participant attributes — constants + diffing publisher.

The frontend (frontend/session) reads these attributes off the agent's remote
participant. We push only on change to avoid LiveKit chatter.
"""
from __future__ import annotations

from typing import Any


ATTR_CURRENT_QUESTION_INDEX = "current_question_index"
ATTR_TOTAL_QUESTIONS = "total_questions"
ATTR_TIME_REMAINING_SECONDS = "time_remaining_seconds"
ATTR_SESSION_OUTCOME = "session_outcome"


class AttributePublisher:
    """Wraps room.local_participant.set_attributes with last-value diffing."""

    def __init__(self, *, room: Any) -> None:
        self._room = room
        self._last: dict[str, str] = {}

    async def publish(self, **attrs: Any) -> dict[str, str]:
        diff: dict[str, str] = {}
        for k, v in attrs.items():
            sv = str(v)
            if self._last.get(k) != sv:
                diff[k] = sv
        if not diff:
            return {}
        await self._room.local_participant.set_attributes(diff)
        self._last.update(diff)
        return diff
