/**
 * Per-(job, stage) toggle for the Tracker board's auto-invite behavior.
 * Stored in localStorage so the recruiter's preference survives reloads
 * but doesn't sync across users (intentional: this is a UX preference,
 * not policy — the durable place for policy is a backend hook on stage
 * transitions).
 *
 * Default = enabled. We only persist the disabled state (`'0'`) so a
 * tenant that never touches the toggle stays on the auto-invite path
 * without populating storage.
 */

const KEY_PREFIX = 'tracker:autoinvite'

export function autoInviteKey(jobId: string, stageId: string): string {
  return `${KEY_PREFIX}:${jobId}:${stageId}`
}

export function readAutoInviteEnabled(jobId: string, stageId: string): boolean {
  if (typeof window === 'undefined') return true
  return window.localStorage.getItem(autoInviteKey(jobId, stageId)) !== '0'
}

export function writeAutoInviteEnabled(
  jobId: string,
  stageId: string,
  enabled: boolean,
): void {
  if (typeof window === 'undefined') return
  const key = autoInviteKey(jobId, stageId)
  if (enabled) {
    window.localStorage.removeItem(key)
  } else {
    window.localStorage.setItem(key, '0')
  }
}
