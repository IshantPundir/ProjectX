from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.interview_engine.frontend_attributes import (
    ATTR_CURRENT_QUESTION_INDEX, ATTR_SESSION_OUTCOME,
    ATTR_TIME_REMAINING_SECONDS, ATTR_TOTAL_QUESTIONS,
    AttributePublisher,
)


def _mock_room():
    room = MagicMock()
    room.local_participant.set_attributes = AsyncMock()
    return room


@pytest.mark.asyncio
async def test_publish_first_call_pushes_all():
    room = _mock_room()
    pub = AttributePublisher(room=room)
    pushed = await pub.publish(current_question_index=0, total_questions=3, time_remaining_seconds=600)
    assert pushed == {
        "current_question_index": "0",
        "total_questions": "3",
        "time_remaining_seconds": "600",
    }
    room.local_participant.set_attributes.assert_awaited_once_with(pushed)


@pytest.mark.asyncio
async def test_publish_second_call_only_pushes_diffs():
    room = _mock_room()
    pub = AttributePublisher(room=room)
    await pub.publish(current_question_index=0, total_questions=3, time_remaining_seconds=600)
    room.local_participant.set_attributes.reset_mock()
    pushed = await pub.publish(current_question_index=0, total_questions=3, time_remaining_seconds=590)
    assert pushed == {"time_remaining_seconds": "590"}
    room.local_participant.set_attributes.assert_awaited_once_with(pushed)


@pytest.mark.asyncio
async def test_publish_skips_empty_diff():
    room = _mock_room()
    pub = AttributePublisher(room=room)
    await pub.publish(current_question_index=0)
    room.local_participant.set_attributes.reset_mock()
    pushed = await pub.publish(current_question_index=0)
    assert pushed == {}
    room.local_participant.set_attributes.assert_not_awaited()


def test_attribute_constants_match_spec():
    assert ATTR_CURRENT_QUESTION_INDEX == "current_question_index"
    assert ATTR_TOTAL_QUESTIONS == "total_questions"
    assert ATTR_TIME_REMAINING_SECONDS == "time_remaining_seconds"
    assert ATTR_SESSION_OUTCOME == "session_outcome"
