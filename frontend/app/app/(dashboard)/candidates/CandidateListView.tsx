'use client'

import Link from 'next/link'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/px'
import { Input } from '@/components/px'
import type {
  AssignmentStatus,
  CandidatesListFilters,
} from '@/lib/api/candidates'
import { useCandidatesList } from '@/lib/hooks/use-candidates-list'

const PAGE_SIZE = 25

const STATUS_CHIPS: {
  key: string
  label: string
  value: AssignmentStatus | ''
}[] = [
  { key: 'all', label: 'All', value: '' },
  { key: 'active', label: 'Active', value: 'active' },
  { key: 'archived', label: 'Archived', value: 'archived' },
  { key: 'hired', label: 'Hired', value: 'hired' },
  { key: 'rejected', label: 'Rejected', value: 'rejected' },
  { key: 'withdrawn', label: 'Withdrawn', value: 'withdrawn' },
]

export interface CandidateListViewProps {
  /**
   * URL-state-driven filter values. Kept as a single object so the hook's
   * query key (`['candidates-list', filters]`) stays referentially stable
   * across renders when filter values haven't changed.
   */
  filters: {
    q: string
    status: string
    jobId: string
    stageId: string
    offset: number
  }
  /**
   * Setter shared with `ClientCandidatesPage` that writes through to the URL.
   * Receives a partial patch; pass `null` to clear a key entirely.
   */
  onFiltersChange: (patch: Record<string, string | null>) => void
}

export default function CandidateListView({
  filters,
  onFiltersChange,
}: CandidateListViewProps) {
  // Local state for the search input so typing doesn't round-trip through
  // the URL on every keystroke. We debounce into the URL after 300ms.
  //
  // The URL's `q` is the source of truth — if it changes externally (back
  // button, link, programmatic reset) we want the box to follow. React's
  // "deriving state from a changing prop" pattern uses a second state slot
  // that remembers the previous prop; when it differs we call setState
  // during render (React discards the first render and restarts with the
  // new value). This avoids the `set-state-in-effect` lint rule while
  // still letting local typing win over the URL between debounces.
  const [searchInput, setSearchInput] = useState(filters.q)
  const [prevUrlQ, setPrevUrlQ] = useState(filters.q)
  if (filters.q !== prevUrlQ) {
    setPrevUrlQ(filters.q)
    if (filters.q !== searchInput) {
      setSearchInput(filters.q)
    }
  }

  // Debounce writes of the search box into the URL state.
  useEffect(() => {
    if (searchInput === filters.q) return
    const t = setTimeout(() => {
      // Reset offset when search changes — otherwise we land on an empty page.
      onFiltersChange({ q: searchInput || null, offset: null })
    }, 300)
    return () => clearTimeout(t)
  }, [searchInput, filters.q, onFiltersChange])

  // Memoize the filters object passed to the hook so the query key stays
  // reference-stable. `useCandidatesList` uses `['candidates-list', filters]`.
  const hookFilters: CandidatesListFilters = useMemo(
    () => ({
      q: filters.q || undefined,
      status: filters.status || undefined,
      job_id: filters.jobId || undefined,
      stage_id: filters.stageId || undefined,
      offset: filters.offset || 0,
      limit: PAGE_SIZE,
    }),
    [
      filters.q,
      filters.status,
      filters.jobId,
      filters.stageId,
      filters.offset,
    ],
  )

  const { data, isLoading, error } = useCandidatesList(hookFilters)

  // Surface fetch errors via toast (once per error instance).
  const lastErrorRef = useRef<Error | null>(null)
  useEffect(() => {
    if (error && error !== lastErrorRef.current) {
      lastErrorRef.current = error as Error
      toast.error((error as Error).message)
    }
    if (!error) lastErrorRef.current = null
  }, [error])

  const anyFilterApplied =
    !!filters.q ||
    !!filters.status ||
    !!filters.jobId ||
    !!filters.stageId

  const total = data?.total ?? 0
  const offset = data?.offset ?? filters.offset ?? 0
  const limit = data?.limit ?? PAGE_SIZE
  const pageStart = total === 0 ? 0 : offset + 1
  const pageEnd = Math.min(offset + limit, total)
  const canPrev = offset > 0
  const canNext = offset + limit < total

  return (
    <div>
      {/* Filters row: search + status chips */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="w-full sm:w-80">
          <Input
            type="search"
            placeholder="Search name or email"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            aria-label="Search candidates"
          />
        </div>
        <div
          className="inline-flex flex-wrap items-center gap-1 rounded-md border p-0.5"
          role="group"
          aria-label="Filter by assignment status"
          style={{
            background: 'var(--px-surface-2)',
            borderColor: 'var(--px-hairline)',
          }}
        >
          {STATUS_CHIPS.map((chip) => {
            const active = (filters.status || '') === chip.value
            return (
              <button
                key={chip.key}
                type="button"
                onClick={() =>
                  onFiltersChange({
                    status: chip.value || null,
                    offset: null,
                  })
                }
                className="rounded-sm px-3 py-1 text-[12px] transition-colors"
                style={{
                  background: active ? 'var(--px-surface)' : 'transparent',
                  color: active ? 'var(--px-fg)' : 'var(--px-fg-3)',
                  fontWeight: active ? 500 : 400,
                  boxShadow: active ? '0 1px 2px rgba(0,0,0,0.06)' : 'none',
                }}
                aria-pressed={active}
              >
                {chip.label}
              </button>
            )
          })}
        </div>
      </div>

      {/* Body: loading / empty / table */}
      {isLoading ? (
        <div className="text-sm" style={{ color: 'var(--px-fg-3)' }}>Loading…</div>
      ) : !data || data.items.length === 0 ? (
        <div
          className="rounded-[10px] border p-12 text-center"
          style={{
            background: 'var(--px-surface)',
            borderColor: 'var(--px-hairline)',
          }}
        >
          <p className="text-sm" style={{ color: 'var(--px-fg-3)' }}>
            {anyFilterApplied
              ? 'No candidates match your filters.'
              : 'No candidates yet. Click Add Candidate to create one.'}
          </p>
        </div>
      ) : (
        <div
          className="overflow-hidden rounded-[10px] border"
          style={{
            background: 'var(--px-surface)',
            borderColor: 'var(--px-hairline)',
          }}
        >
          <table className="w-full">
            <thead
              style={{
                background: 'var(--px-bg-2)',
                borderBottom: '1px solid var(--px-hairline)',
              }}
            >
              <tr>
                {['Name', 'Email', 'Current title', 'Location', 'Created', 'Assignments'].map(
                  (h) => (
                    <th
                      key={h}
                      className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase"
                      style={{
                        letterSpacing: '0.8px',
                        color: 'var(--px-fg-4)',
                      }}
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {data.items.map((c, i) => (
                <tr
                  key={c.id}
                  style={{
                    borderBottom:
                      i < data.items.length - 1
                        ? '1px solid var(--px-hairline)'
                        : 'none',
                  }}
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <Link
                        href={`/candidates/${c.id}`}
                        className="text-[13px] font-medium hover:underline"
                        style={{ color: 'var(--px-fg)' }}
                      >
                        {c.name ?? '—'}
                      </Link>
                      {c.source.startsWith('ats_') && (
                        <span
                          className="inline-flex items-center rounded-full border px-1.5 text-[9px] font-medium uppercase"
                          style={{
                            height: 15,
                            letterSpacing: '0.4px',
                            color: 'var(--px-fg-3)',
                            background: 'var(--px-surface-2)',
                            borderColor: 'var(--px-hairline)',
                          }}
                          title={`Imported from ${c.source.replace('ats_', '')}`}
                        >
                          From {c.source.replace('ats_', '')}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-[12.5px]" style={{ color: 'var(--px-fg-2)' }}>
                    {c.email ?? '—'}
                  </td>
                  <td className="px-4 py-3 text-[12.5px]" style={{ color: 'var(--px-fg-2)' }}>
                    {c.current_title ?? '—'}
                  </td>
                  <td className="px-4 py-3 text-[12.5px]" style={{ color: 'var(--px-fg-2)' }}>
                    {c.location ?? '—'}
                  </td>
                  <td
                    className="whitespace-nowrap px-4 py-3 text-[11.5px]"
                    style={{ color: 'var(--px-fg-4)' }}
                  >
                    {new Date(c.created_at).toLocaleDateString()}
                  </td>
                  <td
                    className="px-4 py-3 text-[12.5px]"
                    style={{ color: 'var(--px-fg-4)' }}
                  >
                    —
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination footer */}
      {data && data.items.length > 0 && (
        <div className="mt-4 flex items-center justify-between">
          <p className="text-[11.5px]" style={{ color: 'var(--px-fg-4)' }}>
            Showing {pageStart}–{pageEnd} of {total}
          </p>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!canPrev}
              onClick={() =>
                onFiltersChange({
                  offset: String(Math.max(0, offset - limit)),
                })
              }
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!canNext}
              onClick={() =>
                onFiltersChange({ offset: String(offset + limit) })
              }
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
