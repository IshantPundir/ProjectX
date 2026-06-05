"""Gen-3 Ear — turn-audio buffer + Smart Turn v3 predict bridge (B2).

Responsibilities
----------------
TurnAudioBuffer is a stateful ring-buffer for the candidate's audio during
an active turn (speech-start → VAD pause). On every VAD pause event the Ear
calls ``predict()`` to ask the Smart Turn v3 model "did the voice sound
finished?" on the buffered audio. The resulting probability is then fed into
the fusion ladder (``ear/ladder.py``, B1) alongside VAD silence duration and
the MultilingualModel text-EOU probability.

Design notes
------------
*   The buffer stores at most ``max_seconds`` of audio (default 8 s). When
    more audio arrives the oldest samples are discarded — only the TAIL
    (most recent slice) is retained. This matches how the Smart Turn v3 model
    was trained: it inspects the final 8-second window of an utterance.

*   ``append()`` accepts 1-D float32 mono frames at ``sample_rate``.  The
    LiveKit entrypoint (Phase B4) is responsible for converting incoming
    LiveKit AudioFrame objects to 1-D float32 mono before calling append.

*   The detector is **injectable** — pass a mock at construction time for
    unit tests; if ``None`` the detector is lazily built via
    ``build_smart_turn()`` the first time ``predict()`` is called. The lazy
    import of ``build_smart_turn`` is intentional: it keeps this module
    importable in the FastAPI process (where livekit plugins and ONNX are
    NOT available) without error.

*   No livekit imports anywhere in this file.

*   ``reset()`` must be called at turn start (before the candidate begins
    speaking again) so each predict() call sees only the current turn's audio.

Interface
---------
::

    buf = TurnAudioBuffer(sample_rate=16000, max_seconds=8.0, detector=None)

    # Called by the Ear on every LiveKit audio frame during candidate speech.
    buf.append(frame: np.ndarray)  # 1-D float32 mono @ sample_rate

    # Called by the Ear when VAD reports a pause.
    result = buf.predict()
    # → {"prediction": int (0/1), "probability": float [0.0, 1.0]}
    # → {"prediction": 0, "probability": 0.0}  when buffer is empty

    # Called at the start of a new turn (after commit or session-start).
    buf.reset()

    # Introspection for tests and logging.
    len(buf)        # → int: number of buffered samples
    buf.as_array()  # → np.ndarray: a copy of the buffered audio
"""

from __future__ import annotations

import numpy as np


class TurnAudioBuffer:
    """Stateful audio ring-buffer for one candidate turn.

    Parameters
    ----------
    sample_rate:
        Expected sample rate of incoming frames (Hz). Default 16000.
    max_seconds:
        Maximum duration to retain (seconds). Older samples are dropped.
        Default 8.0, matching the Smart Turn v3 model's training window.
    detector:
        Smart Turn detector object with a
        ``predict(audio: np.ndarray, sample_rate: int) -> dict`` method.
        If ``None``, the detector is lazily constructed via
        ``build_smart_turn()`` on the first ``predict()`` call.
        **Inject a mock in unit tests** — never pass ``None`` from tests,
        as the real detector loads an ONNX model and ML libraries.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        max_seconds: float = 8.0,
        detector: object | None = None,
    ) -> None:
        self.sample_rate: int = sample_rate
        self.max_seconds: float = max_seconds
        self._cap: int = int(max_seconds * sample_rate)
        self._buffer: np.ndarray = np.empty(0, dtype=np.float32)
        self._detector: object | None = detector  # may be None until first predict()

    # ------------------------------------------------------------------
    # Core buffer operations
    # ------------------------------------------------------------------

    def append(self, frame: np.ndarray) -> None:
        """Append a float32 mono audio frame to the internal buffer.

        After appending, the buffer is trimmed to the last ``_cap`` samples
        (the tail) so memory is bounded regardless of turn length.

        Parameters
        ----------
        frame:
            1-D float32 mono audio at ``self.sample_rate``.  Multi-channel
            or non-float32 frames are the caller's responsibility to convert
            before calling — the LiveKit entrypoint (Phase B4) handles this.
        """
        frame_f32 = np.asarray(frame, dtype=np.float32)
        self._buffer = np.concatenate([self._buffer, frame_f32])
        # Trim: keep only the tail (most recent samples).
        if len(self._buffer) > self._cap:
            self._buffer = self._buffer[-self._cap:]

    def predict(self) -> dict:
        """Run Smart Turn inference on the buffered audio.

        Returns the detector's dict directly
        (``{"prediction": int, "probability": float}``).

        If the buffer is empty, returns the zero-contract
        ``{"prediction": 0, "probability": 0.0}`` WITHOUT calling the
        detector — there is no audio to evaluate.

        On the first call with a non-empty buffer and no pre-injected
        detector, ``build_smart_turn()`` is called lazily to construct
        the real ONNX-backed detector.
        """
        if len(self._buffer) == 0:
            # Empty-buffer contract: return a definitive "not done" signal
            # without touching the detector.
            return {"prediction": 0, "probability": 0.0}

        # Lazy construction of the real detector (engine container only).
        if self._detector is None:
            from app.ai.realtime import build_smart_turn  # noqa: PLC0415 (lazy)
            self._detector = build_smart_turn()

        return self._detector.predict(self._buffer, sample_rate=self.sample_rate)  # type: ignore[union-attr]

    def reset(self) -> None:
        """Clear the buffer.

        Call this at turn commit (immediately before the Mouth speaks) and
        at the start of each new candidate turn so ``predict()`` only ever
        sees the current turn's audio.
        """
        self._buffer = np.empty(0, dtype=np.float32)

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        """Number of samples currently in the buffer."""
        return len(self._buffer)

    def as_array(self) -> np.ndarray:
        """Return a copy of the current buffer contents.

        Returns a copy so callers cannot mutate internal state.
        """
        return self._buffer.copy()
