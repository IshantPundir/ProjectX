# Vendored MediaPipe model assets

These models are served same-origin (no CDN at runtime) per the candidate-surface
no-third-party rule. Loaded by `components/interview/proctoring/vision/*`.

| File | Task | Source | License |
|---|---|---|---|
| `face_landmarker.task` | FaceLandmarker (head pose, blink) | MediaPipe model storage | Apache-2.0 |
| `blaze_face_short_range.tflite` | FaceDetector (multi-face count, ~2m) | https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite | Apache-2.0 |

The Tasks API ships no full-range FaceDetector model; far/background faces are the
server-side `vision` (RetinaFace) plane's responsibility. See
`docs/superpowers/specs/2026-06-15-client-proctoring-hardening-design.md` §5.
