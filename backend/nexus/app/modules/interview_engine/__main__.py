"""LiveKit Agents CLI entrypoint. Run with: ``python -m app.modules.interview_engine``
(the nexus-engine compose service spawns the package this way)."""

from livekit.agents import cli

from app.modules.interview_engine.agent import server

if __name__ == "__main__":
    cli.run_app(server)
