'use client'

import type { SignalWithIndex } from './helpers/groupSignals'

// Local copy of the page-level icon helper. The original `I` lives in
// page.tsx and will move with its primary consumer in a later task; until
// then, this rail gets its own minimal copy + just the icon path it needs.
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

const EYE_ICON =
  'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 15a3 3 0 100-6 3 3 0 000 6z'

type SectionId = 'must' | 'nice' | 'snapshot' | 'jd'

export function SectionsRail({
  must,
  nice,
  hasSnapshot,
  totalCount,
  needsReviewCount,
  activeSection,
  filename,
  onShowJd,
  onJump,
}: {
  must: SignalWithIndex[]
  nice: SignalWithIndex[]
  hasSnapshot: boolean
  totalCount: number
  needsReviewCount: number
  activeSection: SectionId | null
  filename: string
  onShowJd: () => void
  onJump: (section: SectionId) => void
}) {
  // Mirror the order of actual sections in the center canvas, and hide
  // groups that aren't present for this role.
  const sections: { id: SectionId; label: string; count: number }[] = []
  if (must.length > 0)
    sections.push({ id: 'must', label: 'Must-haves', count: must.length })
  if (nice.length > 0)
    sections.push({ id: 'nice', label: 'Nice-to-haves', count: nice.length })
  if (hasSnapshot)
    sections.push({ id: 'snapshot', label: 'Role snapshot', count: 1 })
  sections.push({ id: 'jd', label: 'Full JD', count: 1 })

  return (
    <aside
      className="sticky self-start flex flex-col overflow-y-auto rounded-[10px] border"
      style={{
        // 48px AppShell top bar + 12px gap = 60
        top: 60,
        maxHeight: 'calc(100vh - 72px)',
        background: 'var(--px-bg-2)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div className="px-3.5 pb-2 pt-4">
        <div className="px-eyebrow mb-2.5">Sections</div>
        {sections.map((s) => {
          const active = activeSection === s.id
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => (s.id === 'jd' ? onShowJd() : onJump(s.id))}
              className="mb-0.5 flex h-7 w-full cursor-pointer items-center gap-2 rounded-md border-none px-2.5 text-left text-[13px] transition-colors"
              style={{
                background: active ? 'var(--px-surface)' : 'transparent',
                color: active ? 'var(--px-fg)' : 'var(--px-fg-2)',
                borderLeft: active
                  ? '2px solid var(--px-accent)'
                  : '2px solid transparent',
              }}
              onMouseEnter={(e) => {
                if (!active)
                  e.currentTarget.style.background = 'var(--px-surface-2)'
              }}
              onMouseLeave={(e) => {
                if (!active) e.currentTarget.style.background = 'transparent'
              }}
            >
              <span className="flex-1 truncate">{s.label}</span>
              <span
                className="px-mono text-[11px]"
                style={{
                  color: 'var(--px-fg-4)',
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {s.count}
              </span>
            </button>
          )
        })}
      </div>

      <div className="flex-1" />

      {/* Counts summary */}
      <div
        className="border-t px-3.5 py-2.5"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        <div className="px-eyebrow mb-1.5">Summary</div>
        <div className="text-[12px]" style={{ color: 'var(--px-fg-3)' }}>
          <span
            className="px-mono"
            style={{ color: 'var(--px-fg)', fontVariantNumeric: 'tabular-nums' }}
          >
            {totalCount}
          </span>{' '}
          signals ·{' '}
          <span
            className="px-mono"
            style={{
              color: needsReviewCount > 0 ? 'var(--px-caution)' : 'var(--px-fg-4)',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {needsReviewCount}
          </span>{' '}
          to check
        </div>
      </div>

      {/* Original JD file card */}
      <div
        className="border-t px-3.5 py-3"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        <div className="px-eyebrow mb-2">Original JD</div>
        <button
          type="button"
          onClick={onShowJd}
          className="flex w-full cursor-pointer items-center gap-2.5 rounded-md border p-2 text-left text-[12px]"
          style={{
            background: 'var(--px-surface)',
            borderColor: 'var(--px-hairline)',
            color: 'var(--px-fg-3)',
          }}
        >
          <div
            className="flex items-center justify-center rounded-sm border"
            style={{
              width: 22,
              height: 28,
              background: 'var(--px-bg)',
              borderColor: 'var(--px-hairline-strong)',
            }}
          >
            <span
              className="px-mono text-[8px]"
              style={{ color: 'var(--px-fg-3)' }}
            >
              TXT
            </span>
          </div>
          <div className="min-w-0 flex-1">
            <div
              className="truncate text-[12px]"
              style={{ color: 'var(--px-fg)' }}
            >
              {filename}
            </div>
            <div className="text-[10.5px]" style={{ color: 'var(--px-fg-4)' }}>
              Click to read full
            </div>
          </div>
          <I d={EYE_ICON} size={12} />
        </button>
      </div>
    </aside>
  )
}
