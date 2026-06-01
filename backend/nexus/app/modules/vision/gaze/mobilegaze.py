# app/modules/vision/gaze/mobilegaze.py
"""MobileGaze (yakhyo/gaze-estimation, MIT) ONNX gaze estimator (v1).

`resnet34_gaze.onnx` is Gaze360-trained = NON-COMMERCIAL, dev/POC only
(spec §16.8). Heavy deps (onnxruntime / cv2 / numpy / uniface) import LAZILY in
__init__ so the lean nexus API image can import the module graph without them.

Pipeline (mirrors the MobileGaze ONNX inference): RetinaFace detect → per-face
crop → BGR->RGB, resize to input_size², /255, ImageNet-normalize, CHW, batch →
ONNX → 90-bin softmax expectation (×4 − 180) → degrees → radians.
"""
from __future__ import annotations

import structlog

from app.modules.vision.gaze.base import FaceGaze

log = structlog.get_logger("vision.gaze.mobilegaze")


class MobileGazeEstimator:
    """One instance per worker process (model load + detector init are costly).
    `estimate` returns one FaceGaze per detected face.
    """

    def __init__(
        self,
        *,
        weights_path: str,
        input_size: int = 448,
        pitch_sign: int = 1,
        yaw_sign: int = 1,
        intra_op_threads: int = 1,
    ) -> None:
        # Lazy — only the vision-worker image has these installed.
        import inspect  # noqa: PLC0415

        import numpy as np  # noqa: PLC0415
        import onnxruntime as ort  # noqa: PLC0415
        from uniface.detection import RetinaFace  # noqa: PLC0415

        self._np = np
        # Cap per-inference fan-out: one inference must NOT own the box. Throughput
        # comes from worker process concurrency. (The 2026-06-01 peg was uncapped
        # intra-op threads defaulting to the host core count.)
        so = ort.SessionOptions()
        so.intra_op_num_threads = intra_op_threads
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self._session = ort.InferenceSession(
            weights_path, sess_options=so, providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name
        self._output_names = [o.name for o in self._session.get_outputs()]
        # RetinaFace (uniface) is onnxruntime-backed too, but most uniface
        # versions build their InferenceSession internally WITHOUT exposing
        # SessionOptions — in that case the detector's ORT intra-op threads are
        # bounded ONLY by the worker's cpus cgroup cap (docker-compose), which is
        # the real backstop. Pass our capped options when the version accepts them
        # (best-effort efficiency).
        rf_params = inspect.signature(RetinaFace.__init__).parameters
        if "sess_options" in rf_params:
            self._detector = RetinaFace(sess_options=so)
        elif "session_options" in rf_params:
            self._detector = RetinaFace(session_options=so)
        else:
            self._detector = RetinaFace()
            log.info("vision.gaze.retinaface.uncapped_relying_on_cgroup")
        self._size = (input_size, input_size)
        self._pitch_sign = pitch_sign
        self._yaw_sign = yaw_sign
        self._mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
        self._std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)
        self._idx = np.arange(90, dtype=np.float32)
        log.info("vision.gaze.mobilegaze.loaded", outputs=self._output_names, input_size=input_size)

    def _preprocess(self, crop_bgr):
        import cv2  # noqa: PLC0415

        np = self._np
        img = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, self._size).astype(np.float32) / 255.0
        img = np.transpose(img, (2, 0, 1))
        img = (img - self._mean) / self._std
        return np.expand_dims(img, 0).astype(np.float32)

    def _decode(self, logits):
        """90-bin softmax expectation → radians (binwidth 4°, offset 180°)."""
        np = self._np
        e = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs = e / e.sum(axis=1, keepdims=True)
        deg = float((probs * self._idx).sum(axis=1)[0] * 4.0 - 180.0)
        return float(np.radians(deg))

    def _split_yaw_pitch(self, outs):
        """Map outputs to (yaw_logits, pitch_logits). Prefer output NAMES (robust
        to export order); fall back to L2CS/MobileGaze [pitch, yaw] order.
        VERIFY the sign/zone mapping in the manual D9 test.
        """
        named = dict(zip(self._output_names, outs, strict=False))
        yaw_k = next((k for k in self._output_names if "yaw" in k.lower()), None)
        pitch_k = next((k for k in self._output_names if "pitch" in k.lower()), None)
        if yaw_k is not None and pitch_k is not None:
            return named[yaw_k], named[pitch_k]
        return outs[1], outs[0]  # fallback: [pitch, yaw]

    def estimate(self, frame_bgr) -> list[FaceGaze]:
        h, w = frame_bgr.shape[:2]
        faces = self._detector.detect(frame_bgr)
        out: list[FaceGaze] = []
        for f in faces:
            x1, y1, x2, y2 = (int(v) for v in f.bbox[:4])
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 <= x1 or y2 <= y1:
                continue
            inp = self._preprocess(frame_bgr[y1:y2, x1:x2])
            outs = self._session.run(self._output_names, {self._input_name: inp})
            yaw_logits, pitch_logits = self._split_yaw_pitch(outs)
            out.append(
                FaceGaze(
                    bbox=(float(x1), float(y1), float(x2), float(y2)),
                    pitch=self._decode(pitch_logits) * self._pitch_sign,
                    yaw=self._decode(yaw_logits) * self._yaw_sign,
                    score=float(getattr(f, "confidence", 1.0)),
                )
            )
        return out
