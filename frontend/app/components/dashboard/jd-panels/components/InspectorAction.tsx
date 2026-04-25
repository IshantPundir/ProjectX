'use client'

import { Kbd } from './Kbd'

export function InspectorAction({
  label,
  keys,
  primary,
  danger,
  onClick,
}: {
  label: string
  keys: readonly string[]
  primary?: boolean
  danger?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-7 cursor-pointer items-center gap-2 rounded-md border-none px-2.5 text-[12.5px] transition-colors"
      style={{
        background: primary ? 'var(--px-accent-tint)' : 'transparent',
        color: danger
          ? 'var(--px-danger)'
          : primary
            ? 'var(--px-accent)'
            : 'var(--px-fg-2)',
        border: primary
          ? '1px solid var(--px-accent-line)'
          : '1px solid transparent',
        fontWeight: primary ? 500 : 400,
      }}
      onMouseEnter={(e) => {
        if (!primary) e.currentTarget.style.background = 'var(--px-surface-2)'
      }}
      onMouseLeave={(e) => {
        if (!primary) e.currentTarget.style.background = 'transparent'
      }}
    >
      <span className="flex-1 text-left">{label}</span>
      <Kbd keys={keys} />
    </button>
  )
}
