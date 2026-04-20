'use client'

// Phase 3C will populate real session states
// (created/pre_check/consented/active/completed/cancelled/error). In Phase 3B
// `state` is always `null` on the kanban/list payloads, so we render a neutral
// "Not invited" pill to signal the absence of a session.

interface Props {
  state: string | null
}

const STATE_STYLES: Record<string, { label: string; className: string }> = {
  created: {
    label: 'Created',
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
  const entry = state ? STATE_STYLES[state] ?? NOT_INVITED_STYLE : NOT_INVITED_STYLE
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${entry.className}`}
    >
      {entry.label}
    </span>
  )
}
