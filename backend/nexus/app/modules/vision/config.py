# app/modules/vision/config.py
"""Env-driven vision-proctoring config — single source for model + thresholds.

Mirrors app/ai/config.py discipline: never hardcode the gaze weights path or a
detector threshold elsewhere. Swapping the gaze model (e.g. to clean weights or
a MediaPipe estimator — spec §16.2/§16.8) is an env change.
"""
from __future__ import annotations

from app.config import Settings, settings


class VisionConfig:
    def __init__(self, _settings: Settings | None = None) -> None:
        self._s = _settings if _settings is not None else Settings()

    @property
    def gaze_weights_path(self) -> str:
        return self._s.vision_gaze_weights_path

    @property
    def gaze_arch(self) -> str:
        return self._s.vision_gaze_arch

    @property
    def sample_fps(self) -> float:
        return self._s.vision_sample_fps

    @property
    def zone_yaw_deg(self) -> float:
        return self._s.vision_zone_yaw_deg

    @property
    def zone_pitch_deg(self) -> float:
        return self._s.vision_zone_pitch_deg

    @property
    def far_off_deg(self) -> float:
        return self._s.vision_far_off_deg

    @property
    def off_screen_min_ms(self) -> int:
        return self._s.vision_off_screen_min_ms

    @property
    def down_glance_min_ms(self) -> int:
        return self._s.vision_down_glance_min_ms

    @property
    def down_glance_max_ms(self) -> int:
        return self._s.vision_down_glance_max_ms

    @property
    def reading_window_ms(self) -> int:
        return self._s.vision_reading_window_ms

    @property
    def reading_min_reversals(self) -> int:
        return self._s.vision_reading_min_reversals

    @property
    def multi_face_min_ms(self) -> int:
        return self._s.vision_multi_face_min_ms

    @property
    def band_high_off_screen_pct(self) -> float:
        return self._s.vision_band_high_off_screen_pct

    @property
    def band_medium_off_screen_pct(self) -> float:
        return self._s.vision_band_medium_off_screen_pct

    @property
    def band_high_down_glances(self) -> int:
        return self._s.vision_band_high_down_glances

    @property
    def max_unscorable_pct(self) -> float:
        return self._s.vision_max_unscorable_pct


vision_config = VisionConfig(settings)
