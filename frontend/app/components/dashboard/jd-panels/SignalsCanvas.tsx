'use client'

import { CanvasHeader } from './components/CanvasHeader'
import { EmptyRow } from './components/EmptyRow'
import { SignalGroup } from './components/SignalGroup'
import { SignalRow } from './components/SignalRow'
import { TabStrip } from './components/TabStrip'
import type { SignalWithIndex } from './helpers/groupSignals'

import type { JobPostingWithSnapshot } from '@/lib/api/jobs'

// Local copy of the page-level icon helper. The original `I` lives in
// page.tsx and will move with its primary consumer in a later task; until
// then, this canvas gets its own minimal copy + just the icon path it needs.
function I({
  d,
  size = 14,
  stroke = 1.6,
}: {
  d: string | readonly string[]
  size?: number
  stroke?: number
}) {
  const paths = Array.isArray(d) ? d : [d as string]
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flexShrink: 0 }}
      aria-hidden="true"
    >
      {paths.map((p, i) => (
        <path key={i} d={p} />
      ))}
    </svg>
  )
}

const WARN_ICON =
  'M10.3 3.9L2.7 17a2 2 0 001.7 3h15.2a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0zM12 9v4M12 17h.01'

export function SignalsCanvas({
  must,
  nice,
  job,
  stateBanner,
  isConfirmed,
  canManage,
  isDirty,
  saving,
  confirming,
  totalCount,
  focusIdx,
  onFocus,
  onSave,
  onSaveAndConfirm,
  onReEnrich,
  onReExtract,
  reExtracting,
}: {
  must: SignalWithIndex[]
  nice: SignalWithIndex[]
  job: JobPostingWithSnapshot
  stateBanner: string | null
  isConfirmed: boolean
  canManage: boolean
  isDirty: boolean
  saving: boolean
  confirming: boolean
  totalCount: number
  focusIdx: number | null
  onFocus: (idx: number | null) => void
  onSave: () => void
  onSaveAndConfirm: () => void
  onReEnrich: () => void
  onReExtract: () => void
  reExtracting: boolean
}) {
  return (
    <main
      className="flex min-w-0 flex-col overflow-hidden rounded-[10px] border"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <CanvasHeader job={job} />
      <TabStrip
        totalCount={totalCount}
        isConfirmed={isConfirmed}
        canManage={canManage}
        isDirty={isDirty}
        saving={saving}
        confirming={confirming}
        onSave={onSave}
        onSaveAndConfirm={onSaveAndConfirm}
        onReEnrich={onReEnrich}
        onReExtract={onReExtract}
        reExtracting={reExtracting}
      />

      <div className="px-6 pb-6 pt-4">
        {stateBanner === 'low-confidence' && (
          <div
            className="mb-4 flex items-start gap-3 rounded-md border p-3.5"
            style={{
              background: 'var(--px-caution-bg)',
              borderColor: 'var(--px-caution-line)',
              color: 'var(--px-caution)',
            }}
          >
            <I d={WARN_ICON} size={16} />
            <div
              className="flex-1 text-[13px]"
              style={{ color: 'var(--px-fg-2)', lineHeight: 1.5 }}
            >
              <div
                className="mb-0.5 font-semibold"
                style={{ color: 'var(--px-caution)' }}
              >
                Copilot wasn&apos;t sure on a lot of this one.
              </div>
              The original JD was thin on specifics — I had to guess at several
              skills. Worth a slower read before confirming.
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  className="px-btn outline xs"
                  onClick={onReEnrich}
                >
                  Re-extract from JD
                </button>
              </div>
            </div>
          </div>
        )}

        <SignalGroup
          id="sig-group-must"
          title="Must-haves"
          count={must.length}
          helper="These block progress in the interview"
          emphasis
        >
          {must.map((s) => (
            <SignalRow
              key={s._i}
              s={s}
              rowId={`sig-row-${s._i}`}
              focused={focusIdx === s._i}
              onClick={() => onFocus(focusIdx === s._i ? null : s._i)}
            />
          ))}
          {must.length === 0 && <EmptyRow label="No must-haves yet." />}
        </SignalGroup>

        <SignalGroup
          id="sig-group-nice"
          title="Nice-to-haves"
          count={nice.length}
          helper="Optional follow-ups during the interview"
        >
          {nice.map((s) => (
            <SignalRow
              key={s._i}
              s={s}
              rowId={`sig-row-${s._i}`}
              focused={focusIdx === s._i}
              onClick={() => onFocus(focusIdx === s._i ? null : s._i)}
            />
          ))}
          {nice.length === 0 && <EmptyRow label="No nice-to-haves yet." />}
        </SignalGroup>

        <SignalGroup id="sig-group-snapshot" title="Role snapshot" count={1}>
          <div
            className="grid items-center gap-3 border-b px-3.5 py-2.5"
            style={{
              gridTemplateColumns: '84px 58px 1fr 120px 30px',
              borderColor: 'var(--px-hairline)',
            }}
          >
            <span className="px-chip soft" style={{ height: 20 }}>
              Role
            </span>
            <span />
            <div
              className="text-[13px]"
              style={{ color: 'var(--px-fg)' }}
            >
              <span className="font-medium">
                {job.latest_snapshot?.seniority_level
                  ? job.latest_snapshot.seniority_level
                      .charAt(0)
                      .toUpperCase() +
                    job.latest_snapshot.seniority_level.slice(1)
                  : '—'}
              </span>
              {job.latest_snapshot?.role_summary && (
                <span
                  className="ml-2 text-[12px]"
                  style={{ color: 'var(--px-fg-3)' }}
                >
                  · {job.latest_snapshot.role_summary}
                </span>
              )}
            </div>
            <div />
            <div />
          </div>
        </SignalGroup>

        <div className="h-6" />
      </div>
    </main>
  )
}
