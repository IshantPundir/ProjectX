'use client'

import type { AssignmentStatus } from '@/lib/api/candidates'

const STATUS_STYLES: Record<
  AssignmentStatus,
  { label: string; className: string }
> = {
  active: { label: 'Active', className: 'px-chip ok' },
  archived: { label: 'Archived', className: 'px-chip soft' },
  hired: { label: 'Hired', className: 'px-chip ai' },
  rejected: { label: 'Rejected', className: 'px-chip danger' },
  withdrawn: { label: 'Withdrawn', className: 'px-chip caution' },
}

interface Props {
  status: AssignmentStatus
}

export function StatusBadge({ status }: Props) {
  const entry = STATUS_STYLES[status] ?? { label: status, className: 'px-chip soft' }
  return (
    <span
      className={entry.className}
      style={{ height: 18, padding: '0 7px', fontSize: 10.5 }}
    >
      {entry.label}
    </span>
  )
}
