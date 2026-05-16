'use client'

import Link from 'next/link'

import { TrackerJobCard } from '@/components/dashboard/tracker/TrackerJobCard'
import { useTrackerJobs } from '@/lib/hooks/use-tracker-jobs'

const GRID_COLS = 'repeat(auto-fill, minmax(320px, 1fr))' as const

/**
 * Tracker landing — lists every live role (`status='active'`) so the
 * recruiter can pick one and move candidates through its pipeline.
 *
 * Roles in `signals_confirmed`/`pipeline_built` ("In review") are
 * deliberately excluded — they can't accept candidates yet, so showing
 * them on a candidate-pipeline tracker is misleading. They surface
 * under the "In review" section of `/jobs` instead.
 *
 * No status filter chips: with a single status, filtering by status is
 * meaningless. Future filters (search, owner, recency) can live here
 * when needed.
 */
export default function ClientTrackerLandingPage() {
  const { data, isLoading, error } = useTrackerJobs()
  const jobs = data ?? []

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
            Live roles. Pick one to see candidates and move them through stages.
          </p>
        </div>
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
      ) : jobs.length === 0 ? (
        <EmptyState />
      ) : (
        <div
          className="grid gap-3"
          style={{ gridTemplateColumns: GRID_COLS }}
        >
          {jobs.map((job) => (
            <TrackerJobCard key={job.id} job={job} />
          ))}
        </div>
      )}
    </div>
  )
}

function EmptyState() {
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
        No live boards yet
      </h2>
      <p
        className="mx-auto mb-6 max-w-md text-sm"
        style={{ color: 'var(--px-fg-3)' }}
      >
        Roles you've activated will show up here. Finish setting up a role and
        click Activate to make it live.
      </p>
      <Link href="/jobs" className="px-btn primary sm inline-block">
        View roles →
      </Link>
    </div>
  )
}
