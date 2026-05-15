'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'

import CandidateKanbanView from '@/components/dashboard/tracker/CandidateKanbanView'
import { useJob } from '@/lib/hooks/use-job'
import { useKanbanBoard } from '@/lib/hooks/use-kanban-board'
import { postedAgo } from '@/lib/utils'

const TIP_KEY = 'tracker-board-tip-dismissed'
// Distance from the kanban container's bottom edge to the viewport bottom
// we want to leave free (page bottom padding). pb-10 = 40px.
const BOARD_BOTTOM_GUTTER = 40
const BOARD_MIN_HEIGHT = 320

interface Props {
  jobId: string
}

export function TrackerKanbanPage({ jobId }: Props) {
  const job = useJob(jobId)
  const board = useKanbanBoard(jobId)

  const [tipDismissed, setTipDismissed] = useState(true)
  useEffect(() => {
    // One-shot localStorage read after mount. Initial state stays `true`
    // so SSR/first paint never shows the tip (avoids hydration mismatch);
    // the effect flips it to `false` only when the dismissed flag is
    // absent. The setState-in-effect lint rule warns about this shape, but
    // it is the SSR-correct pattern for hydrating browser-only state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTipDismissed(localStorage.getItem(TIP_KEY) === '1')
  }, [])

  const total = board.data
    ? board.data.stages.reduce((sum, s) => sum + s.candidates.length, 0)
    : null

  // Anchor the kanban container to the viewport bottom so columns fill the
  // visible area instead of shrinking to content. Measured (not a fixed
  // calc) so the offset adapts to whether the tip banner is shown, the
  // header wraps, etc. AppShell deliberately uses body-scroll, so flex
  // fill from the parent isn't an option — we have to compute the height.
  const boardRef = useRef<HTMLDivElement>(null)
  const [boardHeight, setBoardHeight] = useState<number | null>(null)
  useEffect(() => {
    function recalc() {
      const node = boardRef.current
      if (!node) return
      const top = node.getBoundingClientRect().top
      const next = Math.max(
        BOARD_MIN_HEIGHT,
        window.innerHeight - top - BOARD_BOTTOM_GUTTER,
      )
      setBoardHeight(next)
    }
    recalc()
    window.addEventListener('resize', recalc)
    return () => window.removeEventListener('resize', recalc)
    // tipDismissed changes the layout above; job/board data arrival can
    // also reflow (title length, subtitle population). Re-measure on each.
  }, [tipDismissed, job.data?.title, total])

  if (job.error) {
    return (
      <div className="mx-auto max-w-[800px] px-8 pt-12 text-center">
        <h2
          className="px-serif text-2xl"
          style={{ color: 'var(--px-fg)' }}
        >
          This role no longer exists
        </h2>
        <Link href="/tracker" className="px-btn primary sm mt-6 inline-block">
          ← Back to Tracker
        </Link>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[1600px] px-8 pb-10 pt-5">
      {/* Header */}
      <div className="mb-3 flex items-end gap-3">
        <div className="min-w-0">
          <h1
            className="px-serif m-0 truncate text-[24px] font-normal"
            style={{ letterSpacing: '-0.5px', color: 'var(--px-fg)' }}
          >
            {job.data?.title ?? 'Loading…'}
          </h1>
          <div
            className="mt-1 flex items-center gap-2 text-[11.5px]"
            style={{ color: 'var(--px-fg-4)' }}
          >
            {job.data?.org_unit_name && <span>{job.data.org_unit_name}</span>}
            {/* Candidate count is gated on the board response so the
                subtitle never momentarily reads "0 candidates" while
                the kanban data is still in flight. */}
            {total !== null && (
              <>
                {job.data?.org_unit_name && <span>·</span>}
                <span>
                  {total} {total === 1 ? 'candidate' : 'candidates'}
                </span>
              </>
            )}
            {job.data?.updated_at && (
              <>
                <span>·</span>
                <span>last move {postedAgo(job.data.updated_at)}</span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Tip banner */}
      {!tipDismissed && (
        <div
          className="mb-4 flex items-center gap-3 rounded-md border px-3 py-2 text-[12px]"
          style={{
            background: 'var(--px-surface-2)',
            borderColor: 'var(--px-hairline)',
            color: 'var(--px-fg-3)',
          }}
        >
          <span className="flex-1">
            Drag a card across columns to advance a candidate. Click a card to
            open their profile.
          </span>
          <button
            type="button"
            onClick={() => {
              localStorage.setItem(TIP_KEY, '1')
              setTipDismissed(true)
            }}
            className="px-btn ghost xs"
            aria-label="Dismiss tip"
          >
            Got it
          </button>
        </div>
      )}

      <div
        ref={boardRef}
        style={{
          height: boardHeight ?? undefined,
          minHeight: BOARD_MIN_HEIGHT,
        }}
      >
        <CandidateKanbanView jobId={jobId} />
      </div>
    </div>
  )
}
