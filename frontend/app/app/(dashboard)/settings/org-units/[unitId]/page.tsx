'use client'

import { useCallback, useMemo } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { jobsApi, type JobPostingSummary } from '@/lib/api/jobs'
import { useOrgUnit } from '@/lib/hooks/use-org-unit'
import { useOrgUnits } from '@/lib/hooks/use-org-units'
import { isAnyAdmin, useMe } from '@/lib/hooks/use-me'
import type { OrgUnit } from '@/lib/api/org-units'
import { AccessDenied } from '@/components/dashboard/AccessDenied'

import { CompanyDetail } from './CompanyDetail'
import { DivisionDetail } from './DivisionDetail'
import { RegionDetail } from './RegionDetail'
import { TeamDetail } from './TeamDetail'

export default function OrgUnitDetailPage() {
  const params = useParams<{ unitId: string }>()
  const router = useRouter()
  const qc = useQueryClient()
  const unitId = params.unitId

  const meQuery = useMe()
  const unitQuery = useOrgUnit(unitId)
  const allUnitsQuery = useOrgUnits()
  const jobsQuery = useQuery<JobPostingSummary[]>({
    queryKey: ['jobs-list'],
    queryFn: async () => jobsApi.list(await getFreshSupabaseToken()),
    staleTime: 10_000,
  })

  const unit = unitQuery.data ?? null
  const allUnits = useMemo(() => allUnitsQuery.data ?? [], [allUnitsQuery.data])
  const jobs = useMemo(() => jobsQuery.data ?? [], [jobsQuery.data])
  const loading = unitQuery.isLoading || allUnitsQuery.isLoading
  const error = unitQuery.error?.message || allUnitsQuery.error?.message || ''

  const byId = useMemo(() => new Map(allUnits.map((u) => [u.id, u])), [allUnits])

  /**
   * Parent chain ordered root → ... → immediate parent. Used by:
   *  - the Hierarchy sidebar card (renders the tree)
   *  - the inheritance-source resolver in Region / Client account
   *  - the breadcrumb string in the page header
   */
  const parentChain = useMemo<OrgUnit[]>(() => {
    if (!unit) return []
    const chain: OrgUnit[] = []
    let cur = unit.parent_unit_id ? byId.get(unit.parent_unit_id) : null
    const seen = new Set<string>()
    while (cur && !seen.has(cur.id)) {
      seen.add(cur.id)
      chain.unshift(cur)
      cur = cur.parent_unit_id ? byId.get(cur.parent_unit_id) : null
    }
    return chain
  }, [unit, byId])

const subUnits = useMemo(() => {
    if (!unit) return []
    return allUnits.filter((u) => u.parent_unit_id === unit.id)
  }, [unit, allUnits])

  const { openRolesCount, openRolesByChildId, jobsAnchoredHere } = useMemo(() => {
    const raw: Record<string, number> = {}
    const titlesByUnit: Record<string, { id: string; title: string }[]> = {}
    for (const j of jobs) {
      if (j.status === 'draft') continue
      // Skip ATS-imported unlinked jobs (org_unit_id is NULL until a
      // recruiter wires them to a real org unit).
      if (j.org_unit_id === null) continue
      raw[j.org_unit_id] = (raw[j.org_unit_id] ?? 0) + 1
      ;(titlesByUnit[j.org_unit_id] ||= []).push({ id: j.id, title: j.title })
    }
    const childrenOf: Record<string, string[]> = {}
    for (const u of allUnits) {
      if (u.parent_unit_id) (childrenOf[u.parent_unit_id] ||= []).push(u.id)
    }
    const rolled = (id: string): number => {
      let total = raw[id] ?? 0
      for (const cid of childrenOf[id] ?? []) total += rolled(cid)
      return total
    }
    const byChild: Record<string, number> = {}
    if (unit) {
      for (const c of childrenOf[unit.id] ?? []) byChild[c] = rolled(c)
    }
    return {
      openRolesCount: unit ? rolled(unit.id) : 0,
      openRolesByChildId: byChild,
      jobsAnchoredHere: unit ? titlesByUnit[unit.id] ?? [] : [],
    }
  }, [jobs, allUnits, unit])

  /**
   * Optimistic cache write so the freshly-saved unit is reflected in
   * `useOrgUnits()` and `useOrgUnit()` callers without a refetch flicker.
   * The mutation hook also runs `invalidateQueries` so the source of
   * truth is reconciled by the next refetch.
   */
  const handleSaved = useCallback(
    (updated: OrgUnit) => {
      qc.setQueryData<OrgUnit[]>(['org-units'], (prev) =>
        prev ? prev.map((u) => (u.id === updated.id ? updated : u)) : prev,
      )
      qc.setQueryData<OrgUnit>(['org-units', updated.id], updated)
    },
    [qc],
  )

  const onBack = () => router.push('/settings/org-units')

  // RBAC: same gate as /settings/org-units. Non-admins cannot reach
  // the unit detail pages, even via direct URL or a bookmark.
  if (!meQuery.isLoading && !isAnyAdmin(meQuery.data)) {
    return <AccessDenied />
  }

  if (loading) {
    return (
      <div
        className="mx-auto max-w-[1200px] px-8 pt-6 text-sm"
        style={{ color: 'var(--px-fg-3)' }}
      >
        Loading unit…
      </div>
    )
  }

  if (error || !unit) {
    return (
      <div className="mx-auto max-w-[1200px] px-8 pt-6">
        <div
          className="rounded-md border p-4 text-sm"
          style={{
            color: 'var(--px-danger)',
            background: 'var(--px-danger-bg)',
            borderColor: 'var(--px-danger-line)',
          }}
        >
          {error || 'Unit not found'}
        </div>
      </div>
    )
  }

  // Locked stub: backend returned the unit as is_accessible=false because
  // the caller can see it for tree context (it's an ancestor of a unit
  // they hold Admin on) but they can't manage it. Render a minimal,
  // read-only "ask for access" view instead of the full editable detail.
  if (!unit.is_accessible) {
    return (
      <div className="mx-auto max-w-[1200px] px-8 pt-6">
        <button
          onClick={onBack}
          className="mb-4 text-[12px]"
          style={{ color: 'var(--px-fg-3)' }}
        >
          ← Back to org structure
        </button>
        <div
          className="rounded-[10px] border p-6"
          style={{
            background: 'var(--px-bg-2)',
            borderColor: 'var(--px-hairline)',
          }}
        >
          <div
            className="mb-2 text-[10.5px] font-semibold uppercase"
            style={{ letterSpacing: '0.6px', color: 'var(--px-fg-4)' }}
          >
            Locked
          </div>
          <h1
            className="px-serif m-0 text-[26px] font-normal"
            style={{ letterSpacing: '-0.3px', color: 'var(--px-fg)' }}
          >
            {unit.name}
          </h1>
          <p
            className="mt-2 text-[13px]"
            style={{ color: 'var(--px-fg-3)', lineHeight: 1.55 }}
          >
            This {unit.unit_type.replace('_', ' ')} is visible because it
            sits above a unit you have admin access on. You don&apos;t have
            admin rights here, so the details are hidden.
          </p>
          {unit.admin_emails.length > 0 ? (
            <div className="mt-5">
              <div
                className="mb-2 text-[10.5px] font-semibold uppercase"
                style={{ letterSpacing: '0.6px', color: 'var(--px-fg-4)' }}
              >
                Ask one of these admins for access
              </div>
              <ul
                className="overflow-hidden rounded-md border"
                style={{
                  background: 'var(--px-surface)',
                  borderColor: 'var(--px-hairline)',
                }}
              >
                {unit.admin_emails.map((email, i) => (
                  <li
                    key={email}
                    className="px-3 py-2 text-[13px]"
                    style={{
                      color: 'var(--px-fg)',
                      borderBottom:
                        i < unit.admin_emails.length - 1
                          ? '1px solid var(--px-hairline)'
                          : 'none',
                    }}
                  >
                    {email}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p
              className="mt-4 text-[13px]"
              style={{ color: 'var(--px-fg-4)' }}
            >
              No admin is currently assigned to this unit. Contact your
              super admin.
            </p>
          )}
        </div>
      </div>
    )
  }

  if (unit.unit_type === 'company' || unit.unit_type === 'client_account') {
    return (
      <div className="mx-auto max-w-[1200px]">
        <CompanyDetail
          unit={unit}
          isClientAccount={unit.unit_type === 'client_account'}
          parentChain={parentChain}
          subUnits={subUnits}
          openRolesCount={openRolesCount}
          openRolesByChildId={openRolesByChildId}
          onBack={onBack}
          onSaved={handleSaved}
        />
      </div>
    )
  }
  if (unit.unit_type === 'region') {
    return (
      <div className="mx-auto max-w-[1200px]">
        <RegionDetail
          unit={unit}
          parentChain={parentChain}
          subUnits={subUnits}
          openRolesCount={openRolesCount}
          openRolesByChildId={openRolesByChildId}
          onBack={onBack}
          onSaved={handleSaved}
        />
      </div>
    )
  }
  if (unit.unit_type === 'division') {
    return (
      <div className="mx-auto max-w-[1200px]">
        <DivisionDetail
          unit={unit}
          parentChain={parentChain}
          subUnits={subUnits}
          openRolesCount={openRolesCount}
          openRolesByChildId={openRolesByChildId}
          onBack={onBack}
          onSaved={handleSaved}
        />
      </div>
    )
  }
  return (
    <div className="mx-auto max-w-[1200px]">
      <TeamDetail
        unit={unit}
        parentChain={parentChain}
        subUnits={subUnits}
        jobsAnchoredHere={jobsAnchoredHere}
        openRolesCount={openRolesCount}
        onBack={onBack}
        onSaved={handleSaved}
      />
    </div>
  )
}
