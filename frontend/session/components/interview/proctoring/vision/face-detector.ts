import type { FaceDetector } from '@mediapipe/tasks-vision'
import { visionFileset } from './face-landmarker'

/**
 * Minimum detection confidence for a box to count as a face. Raised well above
 * the previous over-permissive 0.3 (and above the SDK's 0.5 default) to cut
 * FALSE POSITIVES — shadows, patterns, posters and background objects that
 * BlazeFace momentarily scores as low-confidence "faces" and which produced
 * spurious `multiple_faces` flags. The candidate's own close, well-lit face
 * scores ~0.8–0.95, so this does NOT affect detecting them; the recall it trims
 * is far/oblique/dim faces, which are the server RetinaFace plane's job anyway
 * (see spec §5). If a real, present face is ever missed (a false
 * `face_not_visible`), dial this back toward 0.5.
 */
export const FACE_DETECTION_MIN_CONFIDENCE = 0.6

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
