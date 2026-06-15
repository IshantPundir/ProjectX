import type { FaceDetector } from '@mediapipe/tasks-vision'
import { visionFileset } from './face-landmarker'

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
      delegate: 'GPU',
    },
    runningMode: 'VIDEO',
    minDetectionConfidence: 0.3, // lowered from 0.5 default to stretch effective range
  })
}

/** Distil a FaceDetector VIDEO result into a count + top confidence. */
export function summarizeDetections(result: {
  detections?: Array<{ categories?: Array<{ score: number }> }>
}): FaceCountSummary {
  const detections = result.detections ?? []
  let topConfidence = 0
  for (const d of detections) {
    const score = d.categories?.[0]?.score ?? 0
    if (score > topConfidence) topConfidence = score
  }
  return { faceCount: detections.length, topConfidence }
}
