/** Vision proctoring violation kinds (client MediaPipe). A subset of the
 * backend ProctoringKind — these are SOFT violations reported through the same
 * controller as the behavioral guards (counted toward the shared limit). */
export type VisionNudgeKind = 'face_not_visible' | 'multiple_faces' | 'looking_away_sustained'

/** How long a condition must hold continuously before it fires one violation
 * (ms, debounce). Tuned live via the debug overlay. */
export const NUDGE_SUSTAIN_MS: Record<VisionNudgeKind, number> = {
  face_not_visible: 2500,
  multiple_faces: 500,
  looking_away_sustained: 1000,
}
