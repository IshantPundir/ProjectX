'use client'

import { useState } from 'react'

import {
  Dialog,
  DialogContent,
  DialogTitle,
} from '@/components/px'
import type { ProctoringAnalysis, RiskBand } from '@/lib/api/reports'
import { useSessionProctoring } from '@/lib/hooks/use-session-proctoring'

const CARD = 'rounded-xl border bg-white p-3.5'

function fmtTime(ms: number): string {
  const s = Math.floor(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

const BAND_LABEL: Record<RiskBand, string> = {
  low: 'LOW',
  medium: 'MEDIUM',
  high: 'HIGH',
  insufficient_data: 'INSUFFICIENT DATA',
}

function bandColor(b: RiskBand | null): string {
  if (b === 'high') return '#b4232a'
  if (b === 'medium') return '#b87503'
  if (b === 'insufficient_data') return '#5b6b73'
  return '#2f7d4f'
}

const KIND_LABEL: Record<string, string> = {
  off_screen_sustained: 'Looked off-screen',
  down_glance: 'Glanced down',
  reading_sweep: 'Reading pattern',
  multiple_faces: 'Multiple faces',
}

export function ProctoringIntegrityPanel({
  sessionId,
  onSeek,
}: {
  sessionId: string | null
  onSeek: (ms: number) => void
}) {
  const { data, isLoading } = useSessionProctoring(sessionId ?? '')
  const [open, setOpen] = useState(false)

  if (!sessionId || isLoading) {
    return (
      <div className={CARD} style={{ borderColor: 'var(--px-hairline)' }}>
        <Header />
        <p className="mt-2 text-[11px]" style={{ color: '#7d929c' }}>Loading…</p>
      </div>
    )
  }

  if (!data || data.status === 'absent' || data.status === 'pending' || data.status === 'running') {
    return (
      <div className={CARD} style={{ borderColor: 'var(--px-hairline)' }}>
        <Header />
        <p className="mt-2 text-[11px]" style={{ color: '#7d929c' }}>
          {data?.status === 'pending' || data?.status === 'running'
            ? 'Analyzing the recording…'
            : 'No proctoring analysis for this session.'}
        </p>
      </div>
    )
  }

  if (data.status === 'failed') {
    return (
      <div className={CARD} style={{ borderColor: 'var(--px-hairline)' }}>
        <Header />
        <p className="mt-2 text-[11px]" style={{ color: '#7d929c' }}>Analysis unavailable for this session.</p>
      </div>
    )
  }

  const band = data.risk_band
  const top = [...data.flagged_intervals].slice(0, 3)

  return (
    <div className={CARD} style={{ borderColor: 'var(--px-hairline)' }}>
      <Header />
      <div className="mt-2 flex items-center gap-2">
        <span
          className="rounded-md px-2 py-0.5 text-[11px] font-bold text-white"
          style={{ background: bandColor(band) }}
        >
          {band ? BAND_LABEL[band] : '—'}
        </span>
        <span className="text-[10px]" style={{ color: '#7d929c' }}>for review, not a decision</span>
      </div>
      <p className="mt-1 text-[10.5px]" style={{ color: '#8aa0ac' }}>
        signal quality: {data.gaze_signal_quality ?? 'n/a'}
      </p>

      {top.length > 0 && (
        <ul className="mt-2 space-y-1">
          {top.map((iv, i) => (
            <li key={i}>
              <button
                type="button"
                onClick={() => onSeek(iv.start_ms)}
                aria-label={`jump to ${fmtTime(iv.start_ms)}`}
                className="w-full rounded-md px-2 py-1 text-left text-[11px] transition-colors hover:bg-[var(--px-ai-bg)]"
              >
                <span className="font-semibold">{KIND_LABEL[iv.kind] ?? iv.kind}</span>
                <span className="ml-1" style={{ color: 'var(--px-ai)' }}>· jump to {fmtTime(iv.start_ms)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-2 text-[11px] font-semibold"
        style={{ color: 'var(--px-ai)' }}
      >
        View full proctoring detail →
      </button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent widthClass="sm:max-w-lg">
          <DialogTitle>Proctoring &amp; Integrity — detail</DialogTitle>
          <DetectorBreakdown data={data} />
          <Heatmap heatmap={data.gaze_heatmap} />
          <FlaggedList
            intervals={data.flagged_intervals}
            onSeek={(ms) => { onSeek(ms); setOpen(false) }}
          />
        </DialogContent>
      </Dialog>
    </div>
  )
}

function Header() {
  return (
    <div className="flex items-center justify-between">
      <h3 className="text-[12px] font-semibold">Proctoring &amp; Integrity</h3>
      <span className="text-[9.5px] uppercase tracking-wide" style={{ color: '#9fb2bc' }}>evidence</span>
    </div>
  )
}

function DetectorBreakdown({ data }: { data: ProctoringAnalysis }) {
  const s = data.detector_summary
  if (!s) return null
  const rows: [string, string][] = [
    ['Off-screen', `${Math.round(s.off_screen_pct * 100)}% of session`],
    ['Down-glances', String(s.down_glance_count)],
    ['Reading sweeps', String(s.reading_sweep_intervals)],
    ['Max faces', String(s.max_faces)],
    ['Signal quality', data.gaze_signal_quality ?? 'n/a'],
  ]
  return (
    <table className="mt-2 w-full text-[12px]">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td className="py-0.5 pr-4 text-[#5b6b73]">{k}</td>
            <td className="py-0.5 font-medium">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Heatmap({ heatmap }: { heatmap: ProctoringAnalysis['gaze_heatmap'] }) {
  if (!heatmap?.grid?.length) return null
  const flat = heatmap.grid.flat()
  const max = Math.max(1, ...flat)
  return (
    <div className="mt-3">
      <p className="mb-1 text-[10.5px] font-semibold uppercase tracking-wide" style={{ color: '#8aa0ac' }}>
        Gaze heatmap (relative to baseline)
      </p>
      <div
        className="inline-grid gap-0.5"
        style={{ gridTemplateColumns: `repeat(${heatmap.grid[0].length}, 22px)` }}
      >
        {heatmap.grid.map((row, y) =>
          row.map((c, x) => (
            <div
              key={`${x}-${y}`}
              title={`${c}`}
              style={{
                width: 22,
                height: 22,
                borderRadius: 3,
                background: `rgba(31,125,79,${c / max})`,
                border: '1px solid rgba(0,0,0,0.06)',
              }}
            />
          )),
        )}
      </div>
      <p className="mt-1 text-[10px]" style={{ color: '#9fb2bc' }}>
        Center = looking at screen. Brighter = more time.
      </p>
    </div>
  )
}

function FlaggedList({
  intervals,
  onSeek,
}: {
  intervals: ProctoringAnalysis['flagged_intervals']
  onSeek: (ms: number) => void
}) {
  if (!intervals.length) {
    return <p className="mt-3 text-[11px]" style={{ color: '#7d929c' }}>No flagged moments.</p>
  }
  return (
    <ul className="mt-3 max-h-[220px] space-y-1 overflow-y-auto">
      {intervals.map((iv, i) => (
        <li key={i}>
          <button
            type="button"
            onClick={() => onSeek(iv.start_ms)}
            className="w-full rounded-md px-2 py-1 text-left text-[11.5px] hover:bg-[var(--px-ai-bg)]"
          >
            <span className="font-semibold">{KIND_LABEL[iv.kind] ?? iv.kind}</span>
            <span className="ml-1" style={{ color: 'var(--px-ai)' }}>· jump to {fmtTime(iv.start_ms)}</span>
            <span className="ml-1" style={{ color: '#9fb2bc' }}>
              ({fmtTime(iv.start_ms)}–{fmtTime(iv.end_ms)})
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}
