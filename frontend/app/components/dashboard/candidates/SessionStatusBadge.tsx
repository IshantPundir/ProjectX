'use client'

// Renders a session-state pill for candidate cards and tables.
// The set of states mirrors the backend session state machine
// (created / pre_check / consented / active / completed / cancelled / error).
// `null` means no session has been created yet for the assignment — we render
// a neutral "Not invited" pill to signal the absence of a session.
// When state='error', the optional errorCode prop drives a labeled suffix:
// "Failed: <human-readable label>" for known codes, or plain "Failed" for
// null / unknown codes (forward-compatible with new backend error codes).

import type { SessionState } from '@/lib/api/scheduler'
import { labelForErrorCode } from '@/components/dashboard/tracker/session-error-labels'

interface Props {
  state: SessionState | string | null
  errorCode?: string | null
}

// 'error' is rendered separately so we can compose "Failed: <labeled code>".
type NonErrorSessionState = Exclude<SessionState, 'error'>

const STATE_STYLES: Record<NonErrorSessionState, { label: string; className: string }> = {
  created: { label: 'Invited', className: 'px-chip soft' },
  pre_check: { label: 'Pre-check', className: 'px-chip ai' },
  consented: { label: 'Consented', className: 'px-chip ai' },
  active: { label: 'Live', className: 'px-chip ok' },
  completed: { label: 'Completed', className: 'px-chip ok' },
  cancelled: { label: 'Cancelled', className: 'px-chip caution' },
}

const NOT_INVITED_STYLE = { label: 'Not invited', className: 'px-chip soft' }

export function SessionStatusBadge({ state, errorCode = null }: Props) {
  if (state === 'error') {
    // labelForErrorCode returns 'Failed' for null/undefined/unknown codes.
    // We only show the "Failed: <label>" suffix for known codes (i.e. when
    // the label is not the generic fallback itself).
    const labeled = labelForErrorCode(errorCode)
    const isKnown = labeled !== 'Failed'
    const label = isKnown ? `Failed: ${labeled}` : 'Failed'
    return (
      <span
        className="px-chip danger"
        style={{ height: 18, padding: '0 7px', fontSize: 10.5 }}
        title={errorCode ?? undefined}
      >
        {label}
      </span>
    )
  }

  const entry =
    state && state in STATE_STYLES
      ? STATE_STYLES[state as NonErrorSessionState]
      : NOT_INVITED_STYLE
  return (
    <span
      className={entry.className}
      style={{ height: 18, padding: '0 7px', fontSize: 10.5 }}
    >
      {entry.label}
    </span>
  )
}
