'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'

import AddCandidateDialog from './AddCandidateDialog'
import CandidateListView from './CandidateListView'

type CandidatesView = 'list' | 'kanban'

function normalizeView(raw: string | null): CandidatesView {
  return raw === 'kanban' ? 'kanban' : 'list'
}

export default function ClientCandidatesPage() {
  const router = useRouter()
  const searchParams = useSearchParams()

  const jd = searchParams.get('jd')
  const view = normalizeView(searchParams.get('view'))
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

  // Stable filters object for <CandidateListView />. Memoizing avoids
  // churning the `['candidates-list', filters]` query key when unrelated
  // state changes on this page.
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

  const kanbanDisabled = !jd

  const [showAddDialog, setShowAddDialog] = useState(false)

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-zinc-900">Candidates</h1>
        <div className="flex items-center gap-3">
          <Button onClick={() => setShowAddDialog(true)}>
            + Add Candidate
          </Button>
        </div>
      </div>

      {/*
        The detail page lands in Task 24. Until then we intentionally don't
        navigate on create — the list view refetches via TanStack Query
        invalidation and the new candidate appears inline. Once Task 24 ships,
        wire `onCreated={(created) => router.push(`/candidates/${created.id}`)}`.
      */}
      <AddCandidateDialog
        open={showAddDialog}
        onOpenChange={setShowAddDialog}
      />

      <div className="flex flex-wrap items-center gap-3 mb-6">
        {/* Placeholder JD picker — real combobox lands in Task 23. */}
        <div className="flex items-center gap-2">
          <label
            htmlFor="candidates-jd-picker"
            className="text-sm font-medium text-zinc-700"
          >
            Job:
          </label>
          <select
            id="candidates-jd-picker"
            disabled
            value={jd ?? ''}
            onChange={(e) => {
              const next = e.target.value
              // When JD changes, the kanban view's semantics break, so reset
              // view to list. Real wiring lands in Task 23.
              updateParams({ jd: next || null, view: null })
            }}
            className="h-8 rounded-lg border border-zinc-200 bg-white px-2 text-sm text-zinc-600 disabled:opacity-50"
            aria-label="Select a job description"
          >
            <option value="">Select a JD…</option>
          </select>
        </div>

        <div
          className="inline-flex items-center rounded-lg border border-zinc-200 bg-white p-0.5"
          role="group"
          aria-label="View toggle"
        >
          <button
            type="button"
            onClick={() => updateParams({ view: null })}
            className={`px-3 py-1 text-sm rounded-md transition-colors ${
              view === 'list'
                ? 'bg-zinc-100 text-zinc-900 font-medium'
                : 'text-zinc-600 hover:text-zinc-900'
            }`}
            aria-pressed={view === 'list'}
          >
            List
          </button>
          <button
            type="button"
            onClick={() => updateParams({ view: 'kanban' })}
            disabled={kanbanDisabled}
            title={kanbanDisabled ? 'Select a JD to enable Kanban' : undefined}
            className={`px-3 py-1 text-sm rounded-md transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
              view === 'kanban'
                ? 'bg-zinc-100 text-zinc-900 font-medium'
                : 'text-zinc-600 hover:text-zinc-900'
            }`}
            aria-pressed={view === 'kanban'}
          >
            Kanban
          </button>
        </div>
      </div>

      {view === 'kanban' ? (
        <div className="bg-white border border-zinc-200 rounded-lg p-12 text-center">
          <p className="text-sm text-zinc-500">
            Kanban view coming in Task 22
          </p>
        </div>
      ) : (
        <CandidateListView
          filters={listFilters}
          onFiltersChange={updateParams}
        />
      )}
    </div>
  )
}
