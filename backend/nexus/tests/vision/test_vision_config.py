from app.config import Settings
from app.modules.vision.config import VisionConfig


def test_vision_config_reads_settings(monkeypatch):
    monkeypatch.setenv("VISION_GAZE_WEIGHTS_PATH", "/weights/L2CSNet_gaze360.pkl")
    monkeypatch.setenv("VISION_GAZE_ARCH", "ResNet50")
    monkeypatch.setenv("VISION_SAMPLE_FPS", "5.0")
    cfg = VisionConfig(Settings())
    assert cfg.gaze_weights_path == "/weights/L2CSNet_gaze360.pkl"
    assert cfg.gaze_arch == "ResNet50"
    assert cfg.sample_fps == 5.0


def test_vision_config_defaults():
    cfg = VisionConfig(Settings())
    # Off-screen sustained default ≥ 2s; band thresholds are present.
    assert cfg.off_screen_min_ms == 2000
    assert cfg.sample_fps == 5.0
    assert 0.0 < cfg.band_high_off_screen_pct <= 1.0
    assert cfg.gaze_input_size == 448
    assert cfg.gaze_arch == "resnet34"
    assert cfg.gaze_pitch_sign in (1, -1)
