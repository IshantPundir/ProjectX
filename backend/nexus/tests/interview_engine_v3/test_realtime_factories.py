import numpy as np


def test_build_smart_turn_predict_keys_and_types():
    from app.ai.realtime import build_smart_turn

    detector = build_smart_turn()
    silence = np.zeros(16000, dtype=np.float32)  # 1s of silence @ 16kHz
    out = detector.predict(silence, sample_rate=16000)

    assert set(out.keys()) >= {"prediction", "probability"}
    assert out["prediction"] in (0, 1)
    assert isinstance(out["prediction"], int)
    assert isinstance(out["probability"], float)
    assert 0.0 <= out["probability"] <= 1.0
