'use client'

import Link from 'next/link'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
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
          className="inline-flex flex-wrap items-center gap-1 rounded-lg border border-zinc-200 bg-white p-0.5"
          role="group"
          aria-label="Filter by assignment status"
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
                className={`px-3 py-1 text-sm rounded-md transition-colors ${
                  active
                    ? 'bg-zinc-100 text-zinc-900 font-medium'
                    : 'text-zinc-600 hover:text-zinc-900'
                }`}
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
        <div className="text-sm text-zinc-500">Loading…</div>
      ) : !data || data.items.length === 0 ? (
        <div className="bg-white border border-zinc-200 rounded-lg p-12 text-center">
          <p className="text-sm text-zinc-500">
            {anyFilterApplied
              ? 'No candidates match your filters.'
              : 'No candidates yet. Click Add Candidate to create one.'}
          </p>
        </div>
      ) : (
        <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden">
          <table className="w-full">
            <thead className="bg-zinc-50 border-b border-zinc-200">
              <tr>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">
                  Name
                </th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">
                  Email
                </th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">
                  Current Title
                </th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">
                  Location
                </th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">
                  Created
                </th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">
                  Assignments
                </th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((c) => (
                <tr
                  key={c.id}
                  className="border-b border-zinc-100 hover:bg-zinc-50"
                >
                  <td className="px-4 py-3">
                    <Link
                      href={`/candidates/${c.id}`}
                      className="text-sm font-medium text-blue-600 hover:underline"
                    >
                      {c.name ?? '—'}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-sm text-zinc-600">
                    {c.email ?? '—'}
                  </td>
                  <td className="px-4 py-3 text-sm text-zinc-600">
                    {c.current_title ?? '—'}
                  </td>
                  <td className="px-4 py-3 text-sm text-zinc-600">
                    {c.location ?? '—'}
                  </td>
                  <td className="px-4 py-3 text-xs text-zinc-500 whitespace-nowrap">
                    {new Date(c.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-3 text-sm text-zinc-500">
                    {/* TODO: surface assignment count once the list response
                        carries it (see follow-up to Task 8). */}
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
        <div className="flex items-center justify-between mt-4">
          <p className="text-xs text-zinc-500">
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
