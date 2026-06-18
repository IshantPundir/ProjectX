import type { FaceDetector } from '@mediapipe/tasks-vision'
import { visionFileset } from './face-landmarker'

/**
 * Minimum detection confidence for a box to count as a face. Set to the SDK's
 * BlazeFace default (0.5): a balance between cutting FALSE POSITIVES — shadows,
 * patterns, posters and background objects that momentarily score as
 * low-confidence "faces" and produced spurious `multiple_faces` flags — and not
 * MISSING a real, present face (a false `face_not_visible`). The candidate's own
 * close, well-lit face scores ~0.8–0.95, well clear of this floor; the recall it
 * trims is far/oblique/dim faces, which are the server RetinaFace plane's job
 * anyway (see spec §5). Spurious `multiple_faces` is further guarded by the
 * 0.5s sustain debounce in nudge-kinds.ts (a transient frame won't fire).
 */
export const FACE_DETECTION_MIN_CONFIDENCE = 0.5

/** Distilled face-count signal from a FaceDetector VIDEO result. */
export interface FaceCountSummary {
  faceCount: number
  /** Highest per-face detection confidence in [0,1] (0 when no face). */
  topConfidence: number
}

/**
 * Lazily create a MediaPipe FaceDetector for live multi-face counting. Uses the
 * officially-supported SHORT-RANGE BlazeFace model (the Tasks API ships no
 * full-range model; far/background faces are the server RetinaFace plane's job —
 * see spec §5). A dedicated detector returns ALL faces in range with boxes,
 * unlike the FaceLandmarker which returns only the dominant face. SAME-ORIGIN.
 */
export async function createFaceDetector(): Promise<FaceDetector> {
  const { FaceDetector } = await import('@mediapipe/tasks-vision')
  const fileset = await visionFileset()
  return FaceDetector.createFromOptions(fileset, {
    baseOptions: {
      modelAssetPath: '/mediapipe/blaze_face_short_range.tflite',
      delegate: 'CPU',
    },
    runningMode: 'VIDEO',
    minDetectionConfidence: FACE_DETECTION_MIN_CONFIDENCE,
  })
}

/**
 * Distil a FaceDetector VIDEO result into a count + top confidence. Only boxes
 * at/above FACE_DETECTION_MIN_CONFIDENCE count toward `faceCount` (the signal
 * that drives `multiple_faces` / `face_not_visible`) — a defensive precision
 * guard that also drops a degenerate box with a missing score, which previously
 * counted as a face. `topConfidence` still reflects the best box seen, so the
 * `quality` signal keeps its information.
 */
export function summarizeDetections(result: {
  detections?: Array<{ categories?: Array<{ score: number }> }>
}): FaceCountSummary {
  const detections = result.detections ?? []
  let faceCount = 0
  let topConfidence = 0
  for (const d of detections) {
    const score = d.categories?.[0]?.score ?? 0
    if (score > topConfidence) topConfidence = score
    if (score >= FACE_DETECTION_MIN_CONFIDENCE) faceCount += 1
  }
  return { faceCount, topConfidence }
}
