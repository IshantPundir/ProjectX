import type { ProctoringKind } from '@/lib/api/candidate-session'

export const HARD_KINDS: ReadonlySet<ProctoringKind> = new Set([
  'tab_switch',
  // focus_loss is now soft (returned within the grace window); focus_abandoned
  // is the hard "grace expired" terminator — mirrors the fullscreen pair.
  'focus_abandoned',
  'fullscreen_abandoned',
  'devtools',
])

export function isHard(kind: ProctoringKind): boolean {
  return HARD_KINDS.has(kind)
}

/** Human-readable phrase for warnings/toasts (no PII). */
export const VIOLATION_LABEL: Record<ProctoringKind, string> = {
  tab_switch: 'switching tabs',
  focus_loss: 'leaving the interview window',
  focus_abandoned: 'leaving the interview window',
  fullscreen_abandoned: 'exiting fullscreen',
  devtools: 'opening developer tools',
  fullscreen_exit: 'exiting fullscreen',
  keyboard: 'keyboard activity',
  multiple_faces: 'having more than one person on camera',
  face_not_visible: 'moving out of the camera view',
  looking_away_sustained: 'looking away from the screen',
}

export type ProctoringTermination = ProctoringKind | 'soft_threshold_exceeded'

/** End-screen sentence fragment for each terminating reason. */
export const PROCTORING_END_LABEL: Record<ProctoringTermination, string> = {
  ...VIOLATION_LABEL,
  soft_threshold_exceeded: 'repeated interview-rule violations',
}
