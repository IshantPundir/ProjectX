'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useMemo, useState } from 'react'

import { JdPicker } from '@/components/dashboard/candidates/JdPicker'
import { Button } from '@/components/px'

import AddCandidateDialog from './AddCandidateDialog'
import CandidateKanbanView from './CandidateKanbanView'
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
            Track applicants through the pipeline. Signal-match kanban per role.
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
              updateParams({ jd: next, view: null })
            }}
          />
        </div>

        <div
          className="inline-flex items-center rounded-md border p-0.5"
          role="group"
          aria-label="View toggle"
          style={{
            background: 'var(--px-surface-2)',
            borderColor: 'var(--px-hairline)',
            height: 30,
          }}
        >
          <button
            type="button"
            onClick={() => updateParams({ view: null })}
            className="rounded-sm px-3 text-[12px] font-medium transition-colors"
            style={{
              height: 24,
              background:
                view === 'list' ? 'var(--px-surface)' : 'transparent',
              color: view === 'list' ? 'var(--px-fg)' : 'var(--px-fg-3)',
              boxShadow:
                view === 'list' ? '0 1px 2px rgba(0,0,0,0.06)' : 'none',
            }}
            aria-pressed={view === 'list'}
          >
            List
          </button>
          <button
            type="button"
            onClick={() => updateParams({ view: 'kanban' })}
            disabled={kanbanDisabled}
            title={kanbanDisabled ? 'Select a role to enable Kanban' : undefined}
            className="rounded-sm px-3 text-[12px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40"
            style={{
              height: 24,
              background:
                view === 'kanban' ? 'var(--px-surface)' : 'transparent',
              color: view === 'kanban' ? 'var(--px-fg)' : 'var(--px-fg-3)',
              boxShadow:
                view === 'kanban' ? '0 1px 2px rgba(0,0,0,0.06)' : 'none',
            }}
            aria-pressed={view === 'kanban'}
          >
            Kanban
          </button>
        </div>
      </div>

      {view === 'kanban' && jd ? (
        <CandidateKanbanView jobId={jd} />
      ) : (
        <CandidateListView
          filters={listFilters}
          onFiltersChange={updateParams}
        />
      )}
    </div>
  )
}
