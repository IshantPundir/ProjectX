import inspect

from app.modules.session.recording import get_session_recording_playback


def test_recording_playback_has_reconcile_flag():
    sig = inspect.signature(get_session_recording_playback)
    assert "reconcile" in sig.parameters
    assert sig.parameters["reconcile"].default is True


def test_load_session_labels_importable():
    from app.modules.reporting.labels import load_session_labels
    assert inspect.iscoroutinefunction(load_session_labels)
