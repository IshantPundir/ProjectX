'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useMemo, useState } from 'react'

import { JdPicker } from '@/components/dashboard/candidates/JdPicker'
import { Button } from '@/components/px'

import AddCandidateDialog from './AddCandidateDialog'
import CandidateListView from './CandidateListView'

export default function ClientCandidatesPage() {
  const router = useRouter()
  const searchParams = useSearchParams()

  const jd = searchParams.get('jd')
  const q = searchParams.get('q') ?? ''
  const status = searchParams.get('status') ?? ''
  const stageId = searchParams.get('stage_id') ?? ''
  const offsetRaw = searchParams.get('offset')
  const offset = offsetRaw ? Math.max(0, Number.parseInt(offsetRaw, 10) || 0) : 0

  const updateParams = useCallback(
    (patch: Record<string, string | null>) => {
      const params = new URLSearchParams(searchParams.toString())
      for (const [key, value] of Object.entries(patch)) {
        if (value === null || value === '') params.delete(key)
        else params.set(key, value)
      }
      const qs = params.toString()
      router.replace(`/candidates${qs ? `?${qs}` : ''}`, { scroll: false })
    },
    [router, searchParams],
  )

  const listFilters = useMemo(
    () => ({
      q,
      status,
      jobId: jd ?? '',
      stageId,
      offset,
    }),
    [q, status, jd, stageId, offset],
  )

  const [showAddDialog, setShowAddDialog] = useState(false)

  return (
    <div className="mx-auto max-w-[1600px] px-8 pb-10 pt-5">
      <div className="mb-5 flex items-end justify-between">
        <div>
          <h1
            className="px-serif m-0 text-[30px] font-normal"
            style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
          >
            Candidates
          </h1>
          <p
            className="mt-1 text-[12.5px]"
            style={{ color: 'var(--px-fg-3)' }}
          >
            Search and triage candidates across roles. Open Tracker to see the
            board view per role.
          </p>
        </div>
        <Button size="sm" onClick={() => setShowAddDialog(true)}>
          + Add candidate
        </Button>
      </div>

      <AddCandidateDialog
        open={showAddDialog}
        onOpenChange={setShowAddDialog}
      />

      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <label
            htmlFor="candidates-jd-picker"
            className="text-[12px] font-medium"
            style={{ color: 'var(--px-fg-2)' }}
          >
            Role:
          </label>
          <JdPicker
            value={jd}
            onChange={(next) => {
              updateParams({ jd: next })
            }}
          />
        </div>
      </div>

      <CandidateListView
        filters={listFilters}
        onFiltersChange={updateParams}
      />
    </div>
  )
}
