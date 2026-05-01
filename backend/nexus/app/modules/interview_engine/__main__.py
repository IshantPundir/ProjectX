"""LiveKit Agents CLI entrypoint.

Run with: ``python -m app.modules.interview_engine``

Equivalent to the old ``backend/interview_engine/agent.py`` ``__main__``
block, but driven via the package's __main__ so the nexus-engine compose
service can spawn it without needing a top-level script path.
"""

from livekit.agents import cli

from app.modules.interview_engine.agent import server


if __name__ == "__main__":
    cli.run_app(server)
