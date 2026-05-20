import type { ProctoringKind } from '@/lib/api/candidate-session'

export const HARD_KINDS: ReadonlySet<ProctoringKind> = new Set([
  'tab_switch',
  'focus_loss',
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
  fullscreen_abandoned: 'exiting fullscreen',
  devtools: 'opening developer tools',
  fullscreen_exit: 'exiting fullscreen',
  keyboard: 'keyboard activity',
}

export type ProctoringTermination = ProctoringKind | 'soft_threshold_exceeded'

/** End-screen sentence fragment for each terminating reason. */
export const PROCTORING_END_LABEL: Record<ProctoringTermination, string> = {
  ...VIOLATION_LABEL,
  soft_threshold_exceeded: 'repeated interview-rule violations',
}
