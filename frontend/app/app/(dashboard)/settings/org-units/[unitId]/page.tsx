'use client'

import { useCallback, useMemo } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { jobsApi, type JobPostingSummary } from '@/lib/api/jobs'
import { useOrgUnit } from '@/lib/hooks/use-org-unit'
import { useOrgUnits } from '@/lib/hooks/use-org-units'
import type { OrgUnit } from '@/lib/api/org-units'

import { CompanyProfileDetail } from './CompanyProfileDetail'
import { DivisionDetail } from './DivisionDetail'
import { RegionDetail } from './RegionDetail'
import { TeamDetail } from './TeamDetail'

export default function OrgUnitDetailPage() {
  const params = useParams<{ unitId: string }>()
  const router = useRouter()
  const qc = useQueryClient()
  const unitId = params.unitId

  const unitQuery = useOrgUnit(unitId)
  const allUnitsQuery = useOrgUnits()
  const jobsQuery = useQuery<JobPostingSummary[]>({
    queryKey: ['jobs-list'],
    queryFn: async () => jobsApi.list(await getFreshSupabaseToken()),
    staleTime: 10_000,
  })

  const unit = unitQuery.data ?? null
  const allUnits = useMemo(
    () => allUnitsQuery.data ?? [],
    [allUnitsQuery.data],
  )
  const jobs = useMemo(
    () => jobsQuery.data ?? [],
    [jobsQuery.data],
  )
  const loading = unitQuery.isLoading || allUnitsQuery.isLoading
  const error = unitQuery.error?.message || allUnitsQuery.error?.message || ''

  const parentPath = useMemo(() => {
    if (!unit) return ''
    const byId = new Map(allUnits.map((u) => [u.id, u]))
    const chain: string[] = []
    let cur = unit.parent_unit_id ? byId.get(unit.parent_unit_id) : null
    while (cur) {
      chain.unshift(cur.name)
      cur = cur.parent_unit_id ? byId.get(cur.parent_unit_id) : null
    }
    return chain.join(' · ')
  }, [unit, allUnits])

  const subUnits = useMemo(() => {
    if (!unit) return []
    return allUnits.filter((u) => u.parent_unit_id === unit.id)
  }, [unit, allUnits])

  const { openRolesCount, openRolesByChildId } = useMemo(() => {
    const raw: Record<string, number> = {}
    for (const j of jobs) {
      if (j.status === 'draft') continue
      raw[j.org_unit_id] = (raw[j.org_unit_id] ?? 0) + 1
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
    }
  }, [jobs, allUnits, unit])

  /**
   * Subcomponent mutations drive cache invalidation through their hooks;
   * this callback is purely for the success toast and the local
   * `allUnits` list to reflect the updated row without a flicker. The
   * hook's invalidate triggers a refetch anyway — the optimistic update
   * below is a UX polish, not a correctness requirement.
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

  if (loading) {
    return (
      <div className="mx-auto max-w-[1200px] px-8 pt-6 text-sm" style={{ color: 'var(--px-fg-3)' }}>
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

  if (unit.unit_type === 'company' || unit.unit_type === 'client_account') {
    return (
      <div className="mx-auto max-w-[1200px]">
        <CompanyProfileDetail
          unit={unit}
          subUnits={subUnits}
          onBack={onBack}
          onSaved={(u) => {
            handleSaved(u)
            toast.success('Changes saved')
          }}
          openRolesCount={openRolesCount}
        />
      </div>
    )
  }
  if (unit.unit_type === 'region') {
    return (
      <div className="mx-auto max-w-[1200px]">
        <RegionDetail
          unit={unit}
          parentPath={parentPath}
          subUnits={subUnits}
          onBack={onBack}
          onSaved={handleSaved}
          openRolesCount={openRolesCount}
        />
      </div>
    )
  }
  if (unit.unit_type === 'division') {
    return (
      <div className="mx-auto max-w-[1200px]">
        <DivisionDetail
          unit={unit}
          parentPath={parentPath}
          subUnits={subUnits}
          onBack={onBack}
          onSaved={handleSaved}
          openRolesCount={openRolesCount}
          openRolesByChildId={openRolesByChildId}
        />
      </div>
    )
  }
  return (
    <div className="mx-auto max-w-[1200px]">
      <TeamDetail
        unit={unit}
        parentPath={parentPath}
        onBack={onBack}
        onSaved={handleSaved}
        openRolesCount={openRolesCount}
      />
    </div>
  )
}
