from app.ai.config import ai_config


def test_probe_cap_default_is_two():
    assert ai_config.engine_probe_cap_per_thread == 2
