'use client'

import { Kbd } from './Kbd'

// Local copy of the page-level icon helper. The original `I` lives in
// page.tsx and will move with its primary consumer in a later task; until
// then, this strip gets its own minimal copy + just the icon paths it needs.
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

const REFRESH_ICON = 'M21 12a9 9 0 11-3-6.7L21 8M21 3v5h-5'
const CHECK_ICON = 'M20 6L9 17l-5-5'

export function TabStrip({
  totalCount,
  isConfirmed,
  canManage,
  isDirty,
  saving,
  confirming,
  onSave,
  onSaveAndConfirm,
  onReEnrich,
  onReExtract,
  reExtracting,
}: {
  totalCount: number
  isConfirmed: boolean
  canManage: boolean
  isDirty: boolean
  saving: boolean
  confirming: boolean
  onSave: () => void
  onSaveAndConfirm: () => void
  onReEnrich: () => void
  onReExtract: () => void
  reExtracting: boolean
}) {
  return (
    <div
      className="flex h-10 flex-shrink-0 items-end gap-0 border-b px-6"
      style={{ background: 'var(--px-bg)', borderColor: 'var(--px-hairline)' }}
    >
      <div
        className="flex h-[39px] items-center gap-1.5 px-3.5 text-[13px] font-semibold"
        style={{
          color: 'var(--px-fg)',
          borderBottom: '2px solid var(--px-accent)',
        }}
      >
        Signals
        <span
          className="px-mono text-[10.5px]"
          style={{ color: 'var(--px-fg-4)', fontVariantNumeric: 'tabular-nums' }}
        >
          {totalCount}
        </span>
      </div>
      <div className="flex-1" />
      <div className="flex items-center gap-1.5 pb-1.5">
        <button
          type="button"
          className="px-btn ghost sm"
          onClick={onReEnrich}
          disabled={isConfirmed}
        >
          <I d={REFRESH_ICON} size={11} />
          Ask Copilot again
        </button>
        {isConfirmed ? (
          <>
            <button
              type="button"
              className="px-btn outline sm"
              onClick={onReExtract}
              disabled={reExtracting}
            >
              <I d={REFRESH_ICON} size={11} />
              {reExtracting ? 'Re-extracting...' : 'Unlock & re-run extraction'}
            </button>
            <button type="button" className="px-btn outline sm" disabled>
              Locked · live
            </button>
          </>
        ) : canManage ? (
          <>
            {isDirty && (
              <button
                type="button"
                className="px-btn outline sm"
                onClick={onSave}
                disabled={saving}
              >
                {saving ? 'Saving…' : 'Save edits'}
              </button>
            )}
            <button
              type="button"
              className="px-btn primary sm"
              onClick={onSaveAndConfirm}
              disabled={saving || confirming}
            >
              <I d={CHECK_ICON} size={11} stroke={2.2} />
              {confirming
                ? 'Confirming…'
                : isDirty
                  ? 'Save & publish'
                  : 'Approve & publish'}
              <Kbd keys={['⌘', '↵']} />
            </button>
          </>
        ) : (
          <span className="text-[11.5px]" style={{ color: 'var(--px-fg-4)' }}>
            Read-only
          </span>
        )}
      </div>
    </div>
  )
}
