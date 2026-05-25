'use client'

import { ReactNode } from 'react'

export type TabItem<T extends string> = {
  value: T
  label: ReactNode
  disabled?: boolean
  hidden?: boolean
  /** Optional tooltip text shown on hover when disabled. */
  disabledHint?: string
}

type Props<T extends string> = {
  value: T
  onChange: (next: T) => void
  items: TabItem<T>[]
  ariaLabel: string
  className?: string
}

/**
 * Segmented-control-style tab control. Visually a row of pill buttons; the
 * selected pill is filled with --px-accent. Hidden items are not rendered.
 * Disabled items render but do not respond to clicks.
 *
 * Use this for in-page view switching (e.g., the JD review center column's
 * Raw / Enriched / Signal details toggle), not for navigation.
 */
export function Tabs<T extends string>({
  value,
  onChange,
  items,
  ariaLabel,
  className,
}: Props<T>) {
  const visible = items.filter((it) => !it.hidden)
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={`inline-flex items-center gap-0.5 rounded-md border p-0.5 ${className ?? ''}`}
      style={{
        background: 'var(--px-surface-2)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      {visible.map((item) => {
        const selected = item.value === value
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-disabled={item.disabled || undefined}
            title={item.disabled ? item.disabledHint : undefined}
            disabled={item.disabled}
            onClick={() => {
              if (item.disabled) return
              onChange(item.value)
            }}
            className="rounded px-3 py-1 text-[12.5px] font-medium transition-colors"
            style={{
              background: selected ? 'var(--px-accent)' : 'transparent',
              color: selected
                ? 'var(--px-accent-ink)'
                : item.disabled
                  ? 'var(--px-fg-4)'
                  : 'var(--px-fg)',
              cursor: item.disabled ? 'not-allowed' : 'pointer',
              opacity: item.disabled ? 0.5 : 1,
            }}
          >
            {item.label}
          </button>
        )
      })}
    </div>
  )
}
