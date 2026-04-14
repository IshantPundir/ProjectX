'use client'

import { useRouter } from 'next/navigation'
import {
  Briefcase,
  Building2,
  Crown,
  ExternalLink,
  Lock,
  MapPin,
  Plus,
  Users,
  X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import type { OrgUnit } from '@/lib/api/org-units'

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

type Props = {
  unit: OrgUnit | null
  onClose: () => void
  onAddChild: (parentId: string) => void
  canAddChild: boolean
}

export function OrgUnitDetailPanel({
  unit,
  onClose,
  onAddChild,
  canAddChild,
}: Props) {
  const router = useRouter()
  if (!unit) return null

  const Icon = TYPE_ICONS[unit.unit_type] ?? Briefcase
  const typeLabel = TYPE_LABELS[unit.unit_type] ?? unit.unit_type

  return (
    <aside className="fixed right-0 top-0 h-full w-[360px] bg-white border-l border-zinc-200 shadow-2xl z-40 flex flex-col">
      {/* Header */}
      <div className="px-5 py-4 border-b border-zinc-200 flex items-start gap-3">
        <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-zinc-50 flex items-center justify-center text-zinc-600">
          <Icon className="w-5 h-5" aria-hidden="true" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <h2 className="text-base font-semibold text-zinc-900 truncate">
              {unit.name}
            </h2>
            {unit.is_root && (
              <Crown className="w-4 h-4 text-amber-500" aria-label="Root" />
            )}
            {!unit.is_accessible && (
              <Lock
                className="w-4 h-4 text-zinc-400"
                aria-label="Inaccessible"
              />
            )}
          </div>
          <div className="text-xs text-zinc-500 mt-0.5">{typeLabel}</div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close panel"
          className="flex-shrink-0 p-1 rounded hover:bg-zinc-100 text-zinc-400 hover:text-zinc-900 transition"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
        <section>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400 mb-1.5">
            Members
          </div>
          <div className="text-sm text-zinc-900 inline-flex items-center gap-1.5">
            <Users className="w-4 h-4 text-zinc-400" />
            {unit.member_count}{' '}
            {unit.member_count === 1 ? 'member' : 'members'}
          </div>
        </section>

        {unit.admin_emails.length > 0 && (
          <section>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400 mb-1.5">
              Admins
            </div>
            <ul className="space-y-1 text-sm text-zinc-700">
              {unit.admin_emails.map((email) => (
                <li key={email} className="truncate">
                  {email}
                </li>
              ))}
            </ul>
          </section>
        )}

        {unit.company_profile && (
          <section>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400 mb-1.5">
              Company profile
            </div>
            <dl className="space-y-1.5 text-xs">
              {unit.company_profile.industry && (
                <div>
                  <dt className="text-zinc-400">Industry</dt>
                  <dd className="text-zinc-900">
                    {unit.company_profile.industry}
                  </dd>
                </div>
              )}
              {unit.company_profile.company_stage && (
                <div>
                  <dt className="text-zinc-400">Stage</dt>
                  <dd className="text-zinc-900">
                    {unit.company_profile.company_stage}
                  </dd>
                </div>
              )}
              {unit.company_profile.hiring_bar && (
                <div>
                  <dt className="text-zinc-400">Hiring bar</dt>
                  <dd className="text-zinc-900">
                    {unit.company_profile.hiring_bar}
                  </dd>
                </div>
              )}
            </dl>
          </section>
        )}

        <section>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400 mb-1.5">
            Created
          </div>
          <div className="text-xs text-zinc-600">
            {new Date(unit.created_at).toLocaleDateString(undefined, {
              year: 'numeric',
              month: 'short',
              day: 'numeric',
            })}
            {unit.created_by_email && (
              <span className="text-zinc-400"> · by {unit.created_by_email}</span>
            )}
          </div>
        </section>
      </div>

      {/* Footer actions */}
      {unit.is_accessible && (
        <div className="px-5 py-4 border-t border-zinc-200 space-y-2">
          <button
            type="button"
            onClick={() => router.push(`/settings/org-units/${unit.id}`)}
            className="w-full flex items-center justify-center gap-1.5 bg-zinc-900 text-white px-3.5 py-2 rounded-lg text-sm font-medium hover:bg-zinc-800 transition"
          >
            <ExternalLink className="w-3.5 h-3.5" />
            Open full details
          </button>
          {canAddChild && (
            <button
              type="button"
              onClick={() => onAddChild(unit.id)}
              className="w-full flex items-center justify-center gap-1.5 border border-zinc-300 text-zinc-700 px-3.5 py-2 rounded-lg text-sm font-medium hover:bg-zinc-50 transition"
            >
              <Plus className="w-3.5 h-3.5" />
              Add child unit
            </button>
          )}
        </div>
      )}
    </aside>
  )
}
