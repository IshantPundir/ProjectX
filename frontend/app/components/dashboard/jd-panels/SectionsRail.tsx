'use client'

import type { SignalWithIndex } from './helpers/groupSignals'

type SectionId = 'must' | 'nice' | 'snapshot'

export function SectionsRail({
  must,
  nice,
  hasSnapshot,
  totalCount,
  needsReviewCount,
  activeSection,
  onJump,
}: {
  must: SignalWithIndex[]
  nice: SignalWithIndex[]
  hasSnapshot: boolean
  totalCount: number
  needsReviewCount: number
  activeSection: SectionId | null
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
              onClick={() => onJump(s.id)}
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
    </aside>
  )
}
