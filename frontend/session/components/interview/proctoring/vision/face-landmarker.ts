import type { FaceLandmarker, FilesetResolver } from '@mediapipe/tasks-vision'

type VisionFileset = Awaited<ReturnType<typeof FilesetResolver.forVisionTasks>>

let filesetPromise: Promise<VisionFileset> | null = null

/**
 * Memoized MediaPipe WASM fileset, shared by the FaceLandmarker AND the
 * FaceDetector so the WASM runtime is resolved once. SAME-ORIGIN (public/mediapipe/wasm).
 */
export function visionFileset(): Promise<VisionFileset> {
  filesetPromise ??= (async () => {
    const { FilesetResolver } = await import('@mediapipe/tasks-vision')
    return FilesetResolver.forVisionTasks('/mediapipe/wasm')
  })()
  return filesetPromise
}

/**
 * Lazily create a MediaPipe FaceLandmarker configured for live proctoring.
 * WASM + model are SAME-ORIGIN (public/mediapipe/*). Used ONLY for head pose +
 * blink of the primary face — the authoritative face COUNT is the FaceDetector
 * (see face-detector.ts). Dynamic import keeps the heavy SDK out of the
 * pre-/start bundle (LiveKit-bearing route only).
 */
export async function createFaceLandmarker(): Promise<FaceLandmarker> {
  const { FaceLandmarker } = await import('@mediapipe/tasks-vision')
  const fileset = await visionFileset()
  return FaceLandmarker.createFromOptions(fileset, {
    baseOptions: {
      modelAssetPath: '/mediapipe/face_landmarker.task',
      delegate: 'GPU',
    },
    runningMode: 'VIDEO',
    numFaces: 1, // pose/blink of the primary face only; count comes from FaceDetector
    outputFaceBlendshapes: true, // blink
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
