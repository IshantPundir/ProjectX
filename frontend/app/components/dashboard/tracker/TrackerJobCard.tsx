'use client'

import Link from 'next/link'

import { useKanbanBoard } from '@/lib/hooks/use-kanban-board'
import type { JobPostingSummary, JobStatus } from '@/lib/api/jobs'
import { postedAgo } from '@/lib/utils'

interface Props {
  job: JobPostingSummary
}

// Cycled palette for the stacked stage bar — keeps stages visually
// distinct without depending on a stage-type → color map (which would
// need to grow whenever pipeline-stage v6 ships).
const BAR_COLORS = [
  '#3b82f6', // blue
  '#8b5cf6', // violet
  '#f59e0b', // amber
  '#10b981', // emerald
  '#ec4899', // pink
  '#6b7280', // gray
] as const

function statusPillStyle(status: JobStatus): { label: string; bg: string; fg: string } {
  // Tracker filters to `active`-only upstream (`useTrackerJobs`), so in
  // practice this card only ever receives "live" jobs. The default
  // branch is a defensive fallback in case the filter is ever loosened
  // — it renders the raw status rather than mislabelling.
  if (status === 'active') {
    return { label: 'live', bg: 'rgba(16,185,129,0.12)', fg: '#10b981' }
  }
  return { label: status, bg: 'rgba(113,113,122,0.12)', fg: '#71717a' }
}


export function TrackerJobCard({ job }: Props) {
  const board = useKanbanBoard(job.id)
  const pill = statusPillStyle(job.status)
  const stages = board.data?.stages ?? []
  const total = stages.reduce((sum, s) => sum + s.candidates.length, 0)
  const hasAny = total > 0
  // Distinguish a true empty board from a fetch failure — without this, an
  // errored card looks identical to a card with zero candidates, which is a
  // misleading signal for the recruiter triaging the landing grid.
  const errored = board.isError

  return (
    <Link
      href={`/tracker/${job.id}`}
      className="flex min-h-[180px] flex-col gap-2.5 rounded-[10px] border p-4 transition-shadow hover:shadow-sm"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div
            className="flex items-center gap-2 truncate text-[14.5px] font-semibold"
            style={{ color: 'var(--px-fg)', lineHeight: 1.3 }}
          >
            <span className="truncate">{job.title}</span>
          </div>
          <div
            className="mt-0.5 truncate text-[11.5px]"
            style={{ color: 'var(--px-fg-4)' }}
          >
            {job.org_unit_name ?? '—'}
          </div>
        </div>
        <span
          className="inline-flex items-center rounded-full px-2 text-[9.5px] font-medium uppercase"
          style={{
            height: 18,
            letterSpacing: '0.4px',
            background: pill.bg,
            color: pill.fg,
          }}
        >
          {pill.label}
        </span>
      </div>

      {/* Stacked bar */}
      {board.isLoading ? (
        <div
          data-testid="tracker-card-bar-loading"
          className="animate-pulse rounded"
          style={{ height: 6, background: 'var(--px-surface-2)' }}
        />
      ) : (
        <div
          className="flex overflow-hidden rounded"
          style={{ height: 6, background: 'var(--px-surface-2)' }}
          aria-label={`Candidate distribution: ${total} total`}
        >
          {hasAny ? (
            stages.map((s, i) => {
              const w = (s.candidates.length / total) * 100
              if (w === 0) return null
              return (
                <div
                  key={s.stage_id}
                  style={{
                    width: `${w}%`,
                    background: BAR_COLORS[i % BAR_COLORS.length],
                  }}
                  title={`${s.stage_name}: ${s.candidates.length}`}
                />
              )
            })
          ) : (
            <div className="w-full" style={{ background: 'var(--px-hairline)' }} />
          )}
        </div>
      )}

      {/* Per-stage labels */}
      {stages.length > 0 && (
        <div
          className="flex items-center gap-2 truncate text-[10px]"
          style={{ color: 'var(--px-fg-4)' }}
        >
          {stages.map((s) => (
            <span key={s.stage_id} className="truncate">
              {s.stage_name} {s.candidates.length}
            </span>
          ))}
        </div>
      )}

      <div className="flex-1" />

      {/* Footer */}
      <div
        className="flex items-center justify-between border-t pt-2 text-[11.5px]"
        style={{ borderColor: 'var(--px-hairline)', color: 'var(--px-fg-3)' }}
      >
        {errored ? (
          <span style={{ color: 'var(--px-danger)' }}>Couldn’t load board</span>
        ) : hasAny ? (
          <span>
            <b
              className="px-mono"
              style={{ color: 'var(--px-fg)', fontVariantNumeric: 'tabular-nums' }}
            >
              {total}
            </b>{' '}
            candidates
          </span>
        ) : (
          <span style={{ color: 'var(--px-fg-4)' }}>No candidates yet</span>
        )}
        <span style={{ color: 'var(--px-fg-4)' }}>
          moved {postedAgo(job.updated_at)}
        </span>
      </div>
    </Link>
  )
}
