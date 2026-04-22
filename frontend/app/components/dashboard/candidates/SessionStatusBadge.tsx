'use client'

// Renders a session-state pill for candidate cards and tables.
// The set of states mirrors the backend session state machine
// (created / pre_check / consented / active / completed / cancelled / error).
// `null` means no session has been created yet for the assignment — we render
// a neutral "Not invited" pill to signal the absence of a session.

import type { SessionState } from '@/lib/api/scheduler'

interface Props {
  state: SessionState | string | null
}

const STATE_STYLES: Record<SessionState, { label: string; className: string }> = {
  created: { label: 'Invited', className: 'px-chip soft' },
  pre_check: { label: 'Pre-check', className: 'px-chip ai' },
  consented: { label: 'Consented', className: 'px-chip ai' },
  active: { label: 'Live', className: 'px-chip ok' },
  completed: { label: 'Completed', className: 'px-chip ok' },
  cancelled: { label: 'Cancelled', className: 'px-chip caution' },
  error: { label: 'Error', className: 'px-chip danger' },
}

const NOT_INVITED_STYLE = { label: 'Not invited', className: 'px-chip soft' }

export function SessionStatusBadge({ state }: Props) {
  const entry = state
    ? STATE_STYLES[state as SessionState] ?? NOT_INVITED_STYLE
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
