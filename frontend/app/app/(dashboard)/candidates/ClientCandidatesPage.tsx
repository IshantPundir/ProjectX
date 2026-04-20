'use client'

import { useRouter, useSearchParams } from 'next/navigation'
import { useCallback } from 'react'

import { Button } from '@/components/ui/button'

type CandidatesView = 'list' | 'kanban'

function normalizeView(raw: string | null): CandidatesView {
  return raw === 'kanban' ? 'kanban' : 'list'
}

export default function ClientCandidatesPage() {
  const router = useRouter()
  const searchParams = useSearchParams()

  const jd = searchParams.get('jd')
  const view = normalizeView(searchParams.get('view'))
  // q and status are read here for future tasks (List view / filters) — they
  // participate in the URL-state contract documented in Task 17 even though
  // there's no UI to wire them to yet. Keep them in the derived values so the
  // setter helper preserves them when other dimensions change.
  const q = searchParams.get('q') ?? ''
  const status = searchParams.get('status') ?? ''

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

  const kanbanDisabled = !jd

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-zinc-900">Candidates</h1>
        <div className="flex items-center gap-3">
          {/* Placeholder — real dialog lands in Task 20. */}
          <Button disabled title="Coming in Task 20">
            + Add Candidate
          </Button>
        </div>
      </div>

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

      <div className="bg-white border border-zinc-200 rounded-lg p-12 text-center">
        {view === 'kanban' ? (
          <p className="text-sm text-zinc-500">
            Kanban view coming in Task 22
          </p>
        ) : (
          <p className="text-sm text-zinc-500">List view coming in Task 19</p>
        )}
        {/* Surface the URL-state values for quick visual QA during scaffolding. */}
        <p className="mt-4 text-xs text-zinc-400">
          jd={jd ?? '—'} · view={view} · q={q || '—'} · status={status || '—'}
        </p>
      </div>
    </div>
  )
}
