"""Vision-pipeline perf-verification harness (dev tool).

Times a single, single-process gaze-analysis run over a recording in isolation
from the Dramatiq actor (no queue, no retries, no concurrency) to measure
per-frame and full-run cost. Use it to validate pipeline speed — e.g. confirm
the GPU (onnxruntime CUDA EP) is engaged vs CPU, or quantify decode cost.

Run inside the vision image (it provides cv2 / onnxruntime / uniface):

    docker compose run --rm -e PYTHONPATH=/app -w /app nexus-vision-worker \\
        python scripts/vision_timing.py <recording_s3_key> [max_frames]

Loads the recording from object storage and the gaze config from Settings, so
it exercises the SAME providers / model / sampling the production actor uses.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time

from app.config import settings
from app.modules.vision.config import vision_config as cfg
from app.modules.vision.gaze.mobilegaze import MobileGazeEstimator
from app.modules.vision.sampler import sample_frames
from app.storage import get_object_storage

s3_key = sys.argv[1]
max_frames = int(sys.argv[2]) if len(sys.argv) > 2 else 200


async def _download(dest: str) -> None:
    await get_object_storage().download_to_path(s3_key, dest)


def main() -> None:
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "rec.mp4")
    t0 = time.monotonic()
    asyncio.run(_download(path))
    size_mb = os.path.getsize(path) / 1e6
    print(f"download: {time.monotonic() - t0:.1f}s  size={size_mb:.1f}MB")

    t1 = time.monotonic()
    est = MobileGazeEstimator(
        weights_path=settings.vision_gaze_weights_path,
        input_size=cfg.gaze_input_size,
        intra_op_threads=cfg.ort_intra_op_threads,
        providers=cfg.onnx_providers,
    )
    print(
        f"model_load: {time.monotonic() - t1:.1f}s  "
        f"OMP_NUM_THREADS={os.environ.get('OMP_NUM_THREADS', '<unset>')}  "
        f"gaze_intra_op={cfg.ort_intra_op_threads}  input_size={cfg.gaze_input_size}  "
        f"sample_fps={cfg.sample_fps}  max_frame_width={cfg.max_frame_width}"
    )

    n = 0
    faces_total = 0
    t2 = time.monotonic()
    for _t_ms, frame in sample_frames(
        path, target_fps=cfg.sample_fps, max_frames=max_frames, max_width=cfg.max_frame_width
    ):
        faces_total += len(est.estimate(frame))
        n += 1
    dt = time.monotonic() - t2
    per_ms = (dt / n * 1000.0) if n else 0.0
    full_frames = min(cfg.max_frames, 1668)  # ~14min @ 2fps
    print(
        f"\nRESULT  frames={n}  elapsed={dt:.1f}s  per_frame={per_ms:.0f}ms  "
        f"throughput={n / dt:.2f} f/s  faces_avg={faces_total / max(n, 1):.2f}"
    )
    print(
        f"PROJECTED full run ({full_frames} frames): "
        f"{per_ms * full_frames / 1000:.0f}s ({per_ms * full_frames / 60000:.1f} min)"
    )


if __name__ == "__main__":
    main()
