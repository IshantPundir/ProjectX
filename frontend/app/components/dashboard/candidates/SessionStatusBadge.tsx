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
  created: {
    label: 'Invited',
    className: 'bg-zinc-100 text-zinc-700 ring-1 ring-inset ring-zinc-500/20',
  },
  pre_check: {
    label: 'Pre-check',
    className:
      'bg-indigo-100 text-indigo-800 ring-1 ring-inset ring-indigo-600/20',
  },
  consented: {
    label: 'Consented',
    className: 'bg-blue-100 text-blue-800 ring-1 ring-inset ring-blue-600/20',
  },
  active: {
    label: 'Live',
    className:
      'bg-emerald-100 text-emerald-800 ring-1 ring-inset ring-emerald-600/20',
  },
  completed: {
    label: 'Completed',
    className: 'bg-green-100 text-green-800 ring-1 ring-inset ring-green-600/20',
  },
  cancelled: {
    label: 'Cancelled',
    className:
      'bg-amber-100 text-amber-800 ring-1 ring-inset ring-amber-600/20',
  },
  error: {
    label: 'Error',
    className: 'bg-red-100 text-red-800 ring-1 ring-inset ring-red-600/20',
  },
}

const NOT_INVITED_STYLE = {
  label: 'Not invited',
  className: 'bg-zinc-100 text-zinc-500 ring-1 ring-inset ring-zinc-400/20',
}

export function SessionStatusBadge({ state }: Props) {
  const entry = state
    ? STATE_STYLES[state as SessionState] ?? NOT_INVITED_STYLE
    : NOT_INVITED_STYLE
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${entry.className}`}
    >
      {entry.label}
    </span>
  )
}
