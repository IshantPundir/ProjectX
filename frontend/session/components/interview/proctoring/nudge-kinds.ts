/** Advisory, NON-terminating vision nudges (spec §5.2). Display-only in
 * Plan A — distinct from backend ProctoringKind. */
export type VisionNudgeKind = 'face_not_visible' | 'multiple_faces' | 'looking_away_sustained'

export const NUDGE_LABEL: Record<VisionNudgeKind, string> = {
  face_not_visible: 'please stay in view of the camera',
  multiple_faces: 'only the candidate should be on camera',
  looking_away_sustained: 'please keep your eyes on the screen',
}

/** Sustained-condition duration before a nudge fires (ms). Tune via overlay. */
export const NUDGE_SUSTAIN_MS: Record<VisionNudgeKind, number> = {
  face_not_visible: 2500,
  multiple_faces: 2000,
  looking_away_sustained: 4000,
}
