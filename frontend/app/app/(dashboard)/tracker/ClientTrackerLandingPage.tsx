'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'

import { TrackerJobCard } from '@/components/dashboard/tracker/TrackerJobCard'
import { useTrackerJobs } from '@/lib/hooks/use-tracker-jobs'

type FilterId = 'all' | 'active' | 'in_setup'

const GRID_COLS = 'repeat(auto-fill, minmax(320px, 1fr))' as const

export default function ClientTrackerLandingPage() {
  const { data, isLoading, error } = useTrackerJobs()
  const [filter, setFilter] = useState<FilterId>('all')

  const counts = useMemo(() => {
    const all = data ?? []
    return {
      all: all.length,
      active: all.filter((j) => j.status === 'active').length,
      // "In setup" groups jobs whose pipeline isn't yet running candidates.
      in_setup: all.filter(
        (j) =>
          j.status === 'signals_confirmed' || j.status === 'pipeline_built',
      ).length,
    }
  }, [data])

  const visible = useMemo(() => {
    const all = data ?? []
    if (filter === 'all') return all
    if (filter === 'active') return all.filter((j) => j.status === 'active')
    return all.filter(
      (j) =>
        j.status === 'signals_confirmed' || j.status === 'pipeline_built',
    )
  }, [data, filter])

  return (
    <div className="mx-auto max-w-[1400px] px-8 pb-10 pt-[22px]">
      {/* Header */}
      <div className="mb-5 flex items-end justify-between">
        <div>
          <h1
            className="px-serif m-0 text-[30px] font-normal"
            style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
          >
            Tracker
          </h1>
          <p
            className="mt-1 text-[12.5px]"
            style={{ color: 'var(--px-fg-3)' }}
          >
            Live boards. Pick a role to see candidates and move them through stages.
          </p>
        </div>
      </div>

      {/* Filter chips */}
      <div
        role="group"
        aria-label="Filter by status"
        className="mb-3.5 flex items-center gap-1.5"
      >
        {(
          [
            { id: 'all' as const, label: 'All', n: counts.all },
            { id: 'active' as const, label: 'Active', n: counts.active },
            {
              id: 'in_setup' as const,
              label: 'In setup',
              n: counts.in_setup,
            },
          ]
        ).map((p) => {
          const active = filter === p.id
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => setFilter(p.id)}
              aria-pressed={active}
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border text-[12px] transition-colors"
              style={{
                height: 26,
                padding: '0 10px',
                borderColor: active ? 'var(--px-fg-2)' : 'transparent',
                background: active ? 'var(--px-surface)' : 'transparent',
                color: active ? 'var(--px-fg)' : 'var(--px-fg-3)',
                fontWeight: active ? 500 : 400,
              }}
            >
              {p.label}
              {p.n > 0 && (
                <span
                  className="px-mono text-[10.5px]"
                  style={{
                    color: 'var(--px-fg-4)',
                    fontVariantNumeric: 'tabular-nums',
                  }}
                >
                  {p.n}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Body */}
      {isLoading ? (
        <div
          className="grid gap-3"
          style={{ gridTemplateColumns: GRID_COLS }}
        >
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="animate-pulse rounded-[10px] border"
              style={{
                height: 180,
                background: 'var(--px-surface)',
                borderColor: 'var(--px-hairline)',
              }}
            />
          ))}
        </div>
      ) : error ? (
        <div className="text-sm" style={{ color: 'var(--px-danger)' }}>
          Error: {(error as Error).message}
        </div>
      ) : visible.length === 0 ? (
        <EmptyState isFiltered={filter !== 'all'} />
      ) : (
        <div
          className="grid gap-3"
          style={{ gridTemplateColumns: GRID_COLS }}
        >
          {visible.map((job) => (
            <TrackerJobCard key={job.id} job={job} />
          ))}
        </div>
      )}
    </div>
  )
}

function EmptyState({ isFiltered }: { isFiltered: boolean }) {
  return (
    <div
      className="rounded-[10px] border p-12 text-center"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <h2
        className="px-serif m-0 mb-2 text-xl"
        style={{ color: 'var(--px-fg)' }}
      >
        {isFiltered ? 'No matching roles' : 'No live boards yet'}
      </h2>
      <p
        className="mx-auto mb-6 max-w-md text-sm"
        style={{ color: 'var(--px-fg-3)' }}
      >
        {isFiltered
          ? 'Try a different filter, or switch back to All.'
          : 'Confirm signals and build a pipeline on a role to make it live.'}
      </p>
      {!isFiltered && (
        <Link href="/jobs" className="px-btn primary sm inline-block">
          View roles →
        </Link>
      )}
    </div>
  )
}
