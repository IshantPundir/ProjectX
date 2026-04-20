'use client'

import type { AssignmentStatus } from '@/lib/api/candidates'

// Tailwind-visible class strings per status so JIT picks them up.
// Keys mirror the backend `AssignmentStatus` enum.
const STATUS_STYLES: Record<
  AssignmentStatus,
  { label: string; className: string }
> = {
  active: {
    label: 'Active',
    className: 'bg-green-100 text-green-800 ring-1 ring-inset ring-green-600/20',
  },
  archived: {
    label: 'Archived',
    className: 'bg-zinc-100 text-zinc-700 ring-1 ring-inset ring-zinc-500/20',
  },
  hired: {
    label: 'Hired',
    className: 'bg-blue-100 text-blue-800 ring-1 ring-inset ring-blue-600/20',
  },
  rejected: {
    label: 'Rejected',
    className: 'bg-red-100 text-red-800 ring-1 ring-inset ring-red-600/20',
  },
  withdrawn: {
    label: 'Withdrawn',
    className:
      'bg-amber-100 text-amber-800 ring-1 ring-inset ring-amber-600/20',
  },
}

interface Props {
  status: AssignmentStatus
}

export function StatusBadge({ status }: Props) {
  const entry = STATUS_STYLES[status] ?? {
    label: status,
    className: 'bg-zinc-100 text-zinc-700 ring-1 ring-inset ring-zinc-500/20',
  }
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${entry.className}`}
    >
      {entry.label}
    </span>
  )
}
