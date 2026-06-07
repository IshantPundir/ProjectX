import subprocess
import sys


def test_app_main_does_not_import_livekit():
    # In a FRESH subprocess: importing the FastAPI app must NOT pull livekit
    # (the engine's livekit-bearing agent.py must stay lazy).
    code = (
        "import app.main, sys; "
        "leaked = sorted(m for m in sys.modules if m == 'livekit' or m.startswith('livekit.')); "
        "assert not leaked, f'livekit leaked into FastAPI import: {leaked}'; "
        "print('OK')"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_run_is_lazily_importable():
    # The lazy livekit-bearing export resolves (this DOES pull livekit — fine, it's the engine path).
    from app.modules.interview_engine import run
    assert callable(run)


def test_core_packages_import():
    import app.modules.interview_engine.brain  # noqa: F401
    import app.modules.interview_engine.mouth  # noqa: F401
    from app.modules.interview_engine.notes import NoteLog  # noqa: F401
    from app.modules.interview_engine.turn_source import (  # noqa: F401
        CommittedTurnSource,
    )
    assert NoteLog is not None
