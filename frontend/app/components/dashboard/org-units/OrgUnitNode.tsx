'use client'

import { memo } from 'react'
import { Handle, Position, type NodeProps } from '@xyflow/react'
import {
  Briefcase,
  Building2,
  Crown,
  Lock,
  MapPin,
  Users,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import type { OrgUnitFlowNode } from './useOrgUnitTreeLayout'

const TYPE_ICONS: Record<string, LucideIcon> = {
  company: Building2,
  division: Briefcase,
  client_account: Briefcase,
  region: MapPin,
  team: Users,
}

const TYPE_LABELS: Record<string, string> = {
  company: 'Company',
  division: 'Division',
  client_account: 'Client Account',
  region: 'Region',
  team: 'Team',
}

// Left accent bar color per unit type.
const TYPE_ACCENT: Record<string, string> = {
  company: 'bg-blue-500',
  division: 'bg-violet-500',
  client_account: 'bg-emerald-500',
  region: 'bg-orange-500',
  team: 'bg-amber-500',
}

const TYPE_TEXT: Record<string, string> = {
  company: 'text-blue-600',
  division: 'text-violet-600',
  client_account: 'text-emerald-600',
  region: 'text-orange-600',
  team: 'text-amber-600',
}

export const OrgUnitNode = memo(function OrgUnitNode({
  data,
  selected,
}: NodeProps<OrgUnitFlowNode>) {
  const { unit } = data
  const Icon = TYPE_ICONS[unit.unit_type] ?? Briefcase
  const accent = TYPE_ACCENT[unit.unit_type] ?? 'bg-zinc-400'
  const typeText = TYPE_TEXT[unit.unit_type] ?? 'text-zinc-600'
  const typeLabel = TYPE_LABELS[unit.unit_type] ?? unit.unit_type

  const inaccessible = !unit.is_accessible
  const isRoot = unit.is_root

  const baseClasses = inaccessible
    ? 'border-zinc-200 bg-zinc-50 opacity-50 cursor-not-allowed'
    : selected
      ? 'border-blue-500 bg-blue-50/60 shadow-lg'
      : 'border-zinc-200 bg-white shadow-sm hover:border-zinc-300 hover:shadow-md'

  return (
    <div
      className={`relative w-[280px] rounded-xl border overflow-hidden transition-all ${baseClasses}`}
      aria-label={`${typeLabel}: ${unit.name}`}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!bg-transparent !border-0 !w-1 !h-1"
      />
      <Handle
        type="source"
        position={Position.Bottom}
        className="!bg-transparent !border-0 !w-1 !h-1"
      />

      {/* Left accent bar */}
      <div
        className={`absolute left-0 top-0 bottom-0 w-1 ${accent}`}
        aria-hidden="true"
      />

      <div className="flex items-center gap-3 pl-4 pr-3 py-3.5 min-h-[96px]">
        {/* Icon bubble */}
        <div
          className={`flex-shrink-0 w-10 h-10 rounded-lg bg-zinc-50 flex items-center justify-center ${typeText}`}
        >
          <Icon className="w-5 h-5" aria-hidden="true" />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <div className="text-sm font-semibold text-zinc-900 truncate">
              {unit.name}
            </div>
            {isRoot && (
              <Crown
                className="w-3.5 h-3.5 text-amber-500 flex-shrink-0"
                aria-label="Root (company) unit"
              />
            )}
            {inaccessible && (
              <Lock
                className="w-3.5 h-3.5 text-zinc-400 flex-shrink-0"
                aria-label="Inaccessible"
              />
            )}
          </div>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className={`text-[10px] font-medium ${typeText}`}>
              {typeLabel}
            </span>
            <span className="text-zinc-300">·</span>
            <span className="text-xs text-zinc-500 inline-flex items-center gap-1">
              <Users className="w-3 h-3" aria-hidden="true" />
              {unit.member_count}{' '}
              {unit.member_count === 1 ? 'member' : 'members'}
            </span>
          </div>
          {unit.admin_emails.length > 0 && !inaccessible && (
            <div className="text-[10px] text-zinc-400 mt-1 truncate">
              {unit.admin_emails.slice(0, 2).join(', ')}
              {unit.admin_emails.length > 2 &&
                ` +${unit.admin_emails.length - 2}`}
            </div>
          )}
        </div>
      </div>
    </div>
  )
})
