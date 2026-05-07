from unittest.mock import patch

from app.modules.interview_engine.stt_factory import build_stt_plugin_for_session


def test_v1_passes_through_to_global_factory():
    sentinel = object()
    with patch("app.modules.interview_engine.stt_factory.build_stt_plugin",
               return_value=sentinel):
        result = build_stt_plugin_for_session(session_config=None)
    assert result is sentinel
