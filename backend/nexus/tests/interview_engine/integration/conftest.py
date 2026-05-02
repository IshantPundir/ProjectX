"""Shared fixtures for InterviewController integration tests.

The plan's literal sketch uses LiveKit's ``AgentSession`` testing
primitives (``session.run(user_input=...)`` + ``mock_tools(...)``) with
the cheap LLM. In practice that ran flaky against the live data bank
because:

  * ``AgentSession.start(controller)`` requires a room — calling
    ``self.session.room_io.room`` blows up with no room attached, and
    several controller paths (progress publish, outcome publish) catch
    this only after a Sentry-level warning fires. Tests pass but the
    log is noisy.
  * Real-LLM-driven 6-question flow is non-deterministic; covering it
    with ``mock_tools`` only addresses the tools, not the dispatch
    cadence.

The integration tests in this directory therefore drive the controller's
async surface directly (``ctrl.on_enter``, ``ctrl._dispatch_task``,
``ctrl.flag_safety_concern(...)``) with ``build_task_for`` /
``record_session_result`` / ``get_bypass_session`` patched. This keeps
each test deterministic, fast, and free of the room-required surface.

Real-LLM-in-the-loop verification lives in the prompt-quality suite
(Phase 2 Task 13) and the end-to-end checklist (Task 16).
"""

from __future__ import annotations

import os

import pytest


def _require_openai_key() -> None:
    """Skip helper for any future real-LLM-driven test."""
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set; skipping integration tests")
