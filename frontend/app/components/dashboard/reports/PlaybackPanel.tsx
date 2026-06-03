'use client'

import { useState } from 'react'

import type { ReportRead } from '@/lib/api/reports'
import { ReelCard } from './ReelCard'
import { SessionPlayback } from './SessionPlayback'

type Mode = 'interview' | 'reel'

/**
 * Report-page playback slot with a toggle between the full session recording
 * (opens the Review Theater) and the AI-directed candidate reel.
 */
export function PlaybackPanel({
  report,
  candidateName,
  onOpenTheater,
}: {
  report: ReportRead
  candidateName: string
  onOpenTheater: () => void
}) {
  const [mode, setMode] = useState<Mode>('interview')

  return (
    <div className="space-y-2.5">
      <div
        className="inline-flex rounded-lg p-0.5"
        role="tablist"
        aria-label="Playback mode"
        style={{ background: 'var(--px-surface-2)', border: '1px solid var(--px-hairline)' }}
      >
        <ToggleBtn active={mode === 'interview'} onClick={() => setMode('interview')}>
          Full interview
        </ToggleBtn>
        <ToggleBtn active={mode === 'reel'} onClick={() => setMode('reel')}>
          Highlight reel
        </ToggleBtn>
      </div>

      {mode === 'interview' ? (
        <SessionPlayback report={report} onOpen={onOpenTheater} />
      ) : (
        <ReelCard sessionId={report.session_id ?? ''} candidateName={candidateName} />
      )}
    </div>
  )
}

function ToggleBtn({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className="rounded-[6px] px-3 py-1 text-[12px] font-semibold transition-colors"
      style={{
        background: active ? 'var(--px-surface)' : 'transparent',
        color: active ? 'var(--px-fg)' : 'var(--px-fg-4)',
        boxShadow: active ? 'var(--px-shadow-sm, 0 1px 2px rgba(0,0,0,0.08))' : 'none',
      }}
    >
      {children}
    </button>
  )
}
