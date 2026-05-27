"""LiveKit Agents CLI entrypoint. Run with: ``python -m app.modules.interview_engine``
(the nexus-engine compose service spawns the package this way).

Note: the Dramatiq broker is bound in ``agent.py`` (imported below), NOT here —
LiveKit runs each interview in a spawned job subprocess that imports ``agent`` but
never executes this ``__main__``, so the broker setup must live where the job
process will load it. See ``app/modules/interview_engine/agent.py``.
"""

from livekit.agents import cli

from app.modules.interview_engine.agent import server

if __name__ == "__main__":
    cli.run_app(server)
