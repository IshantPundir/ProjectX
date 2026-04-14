'use client'

import { useMemo } from 'react'

import type { OrgUnit } from '@/lib/api/org-units'

const TYPE_LABELS: Record<string, string> = {
  company: 'Company',
  division: 'Division',
  client_account: 'Client Account',
  region: 'Region',
  team: 'Team',
}

function IconUsers({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={2}
      stroke="currentColor"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z"
      />
    </svg>
  )
}

function IconChevron({ className = 'w-3 h-3' }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={2.5}
      stroke="currentColor"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M8.25 4.5l7.5 7.5-7.5 7.5"
      />
    </svg>
  )
}

function buildTree(units: OrgUnit[]): { unit: OrgUnit; depth: number }[] {
  const childrenMap = new Map<string | null, OrgUnit[]>()
  for (const u of units) {
    childrenMap.set(u.parent_unit_id, [
      ...(childrenMap.get(u.parent_unit_id) || []),
      u,
    ])
  }
  const result: { unit: OrgUnit; depth: number }[] = []
  function walk(parentId: string | null, depth: number) {
    for (const child of childrenMap.get(parentId) || []) {
      result.push({ unit: child, depth })
      walk(child.id, depth + 1)
    }
  }
  walk(null, 0)
  return result
}

type Props = {
  units: OrgUnit[]
  onUnitClick: (unitId: string) => void
}

export function OrgUnitTreeListFallback({ units, onUnitClick }: Props) {
  const tree = useMemo(() => buildTree(units), [units])

  return (
    <div className="bg-white border border-zinc-200 rounded-xl divide-y divide-zinc-100">
      {tree.map(({ unit: u, depth }) =>
        u.is_accessible ? (
          <button
            key={u.id}
            type="button"
            onClick={() => onUnitClick(u.id)}
            className="w-full flex items-center justify-between py-3.5 pr-4 hover:bg-zinc-50 cursor-pointer transition-colors duration-100 text-left group first:rounded-t-xl last:rounded-b-xl"
            style={{ paddingLeft: `${depth * 24 + 16}px` }}
          >
            <div className="flex items-center gap-2.5 min-w-0">
              {depth > 0 && (
                <IconChevron className="w-2.5 h-2.5 text-zinc-300 shrink-0" />
              )}
              <span className="text-sm font-medium text-zinc-900 truncate">
                {u.name}
              </span>
              <span className="bg-zinc-100 text-zinc-500 px-1.5 py-0.5 rounded text-[10px] font-medium shrink-0">
                {TYPE_LABELS[u.unit_type] || u.unit_type}
              </span>
            </div>
            <div className="flex items-center gap-4 shrink-0">
              <span className="inline-flex items-center gap-1 text-xs text-zinc-400">
                <IconUsers className="w-3 h-3" />
                {u.member_count}
              </span>
              <svg
                className="w-4 h-4 text-zinc-300 group-hover:text-zinc-500 transition-colors duration-100"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth={2}
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M8.25 4.5l7.5 7.5-7.5 7.5"
                />
              </svg>
            </div>
          </button>
        ) : (
          /* Greyed-out ancestor unit — not clickable */
          <div
            key={u.id}
            className="w-full flex items-center justify-between py-3.5 pr-4 first:rounded-t-xl last:rounded-b-xl opacity-40"
            style={{ paddingLeft: `${depth * 24 + 16}px` }}
          >
            <div className="flex items-center gap-2.5 min-w-0">
              {depth > 0 && (
                <IconChevron className="w-2.5 h-2.5 text-zinc-300 shrink-0" />
              )}
              <span className="text-sm font-medium text-zinc-400 truncate">
                {u.name}
              </span>
              <span className="bg-zinc-100 text-zinc-400 px-1.5 py-0.5 rounded text-[10px] font-medium shrink-0">
                {TYPE_LABELS[u.unit_type] || u.unit_type}
              </span>
            </div>
            <span className="text-xs text-zinc-300">No access</span>
          </div>
        ),
      )}
    </div>
  )
}
