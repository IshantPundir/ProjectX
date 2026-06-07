"""Regression guard: the report-scoring actor must be an async actor.

If it is a sync `asyncio.run()` wrapper, Dramatiq runs it on a throwaway event
loop per message instead of the AsyncIO middleware's persistent loop. The
module-global SQLAlchemy async engine pool binds connections to the loop that
created them, so a throwaway loop reuses a connection bound to a different
(closed) loop → ``RuntimeError: got Future attached to a different loop`` /
``Event loop is closed``. Async actors (jd / question_bank / ats / vision) all
run on the shared middleware loop; this actor must too.
"""
from __future__ import annotations

import inspect

from app.modules.reporting import actors as reporting_actors


def test_score_session_report_is_async_actor():
    fn = reporting_actors.score_session_report.fn
    wrapped = getattr(fn, "__wrapped__", None)  # AsyncIO middleware exposes the coroutine here
    assert wrapped is not None and inspect.iscoroutinefunction(wrapped), (
        "score_session_report must be `async def` (run on the AsyncIO middleware "
        "loop), not a sync asyncio.run() wrapper — see cross-loop asyncpg bug."
    )
