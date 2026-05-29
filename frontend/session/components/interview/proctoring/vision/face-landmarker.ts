import type { FaceLandmarker } from '@mediapipe/tasks-vision'

/**
 * Lazily create a MediaPipe FaceLandmarker configured for live proctoring.
 * WASM + model are SAME-ORIGIN (public/mediapipe/*) — no CDN, per the
 * candidate-surface no-third-party rule. Dynamic import keeps the ~heavy
 * SDK out of the pre-/start bundle (LiveKit-bearing route only).
 */
export async function createFaceLandmarker(): Promise<FaceLandmarker> {
  const { FaceLandmarker, FilesetResolver } = await import('@mediapipe/tasks-vision')
  const fileset = await FilesetResolver.forVisionTasks('/mediapipe/wasm')
  return FaceLandmarker.createFromOptions(fileset, {
    baseOptions: {
      modelAssetPath: '/mediapipe/face_landmarker.task',
      delegate: 'GPU',
    },
    runningMode: 'VIDEO',
    numFaces: 2, // multi-face: detect a second person (spec §7①)
    outputFaceBlendshapes: true, // iris look-direction + blink
    outputFacialTransformationMatrixes: true, // head pose
  })
}

/** Read a named blendshape score (0..1) from a FaceLandmarker category list. */
export function blendshape(
  categories: Array<{ categoryName: string; score: number }> | undefined,
  name: string,
): number {
  if (!categories) return 0
  const c = categories.find((x) => x.categoryName === name)
  return c ? c.score : 0
}
