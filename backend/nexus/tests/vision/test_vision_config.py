from app.config import Settings
from app.modules.vision.config import VisionConfig


def test_vision_config_reads_settings(monkeypatch):
    monkeypatch.setenv("VISION_GAZE_WEIGHTS_PATH", "/weights/L2CSNet_gaze360.pkl")
    monkeypatch.setenv("VISION_GAZE_ARCH", "ResNet50")
    monkeypatch.setenv("VISION_SAMPLE_FPS", "2.0")
    cfg = VisionConfig(Settings())
    assert cfg.gaze_weights_path == "/weights/L2CSNet_gaze360.pkl"
    assert cfg.gaze_arch == "ResNet50"
    assert cfg.sample_fps == 2.0


def test_vision_config_defaults():
    cfg = VisionConfig(Settings())
    # Off-screen sustained default ≥ 2s; band thresholds are present.
    assert cfg.off_screen_min_ms == 2000
    assert cfg.sample_fps == 2.0
    assert 0.0 < cfg.band_high_off_screen_pct <= 1.0
    assert cfg.gaze_input_size == 448
    assert cfg.gaze_arch == "resnet34"
    assert cfg.gaze_pitch_sign in (1, -1)


def test_vision_config_bounded_cpu_defaults():
    cfg = VisionConfig(Settings())
    assert cfg.sample_fps == 2.0
    assert cfg.max_frames == 2000
    assert cfg.max_frame_width == 960
    assert cfg.ort_intra_op_threads == 1


def test_vision_config_bounded_cpu_env_override(monkeypatch):
    monkeypatch.setenv("VISION_MAX_FRAMES", "1500")
    monkeypatch.setenv("VISION_MAX_FRAME_WIDTH", "640")
    monkeypatch.setenv("VISION_ORT_INTRA_OP_THREADS", "2")
    cfg = VisionConfig(Settings())
    assert cfg.max_frames == 1500
    assert cfg.max_frame_width == 640
    assert cfg.ort_intra_op_threads == 2
