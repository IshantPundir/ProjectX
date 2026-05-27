"""LiveKit Agents CLI entrypoint. Run with: ``python -m app.modules.interview_engine``
(the nexus-engine compose service spawns the package this way).

``app.brokers`` is imported FIRST so the Dramatiq broker is bound to
``settings.redis_url`` before any ``@dramatiq.actor`` is imported. The engine is a
third process entry point alongside ``app/main.py`` (API) and ``app/worker.py``
(worker); without this, ``record_session_result``'s best-effort report-scoring
``score_session_report.send()`` falls back to Dramatiq's DEFAULT broker at
``localhost:6379`` and fails (the real broker is the ``redis`` compose service).
See ``app/brokers.py`` for the full rationale.
"""

from app import brokers  # noqa: F401  — sets the Dramatiq broker; MUST precede actor imports

from livekit.agents import cli

from app.modules.interview_engine.agent import server

if __name__ == "__main__":
    cli.run_app(server)
