'use client'

import type React from 'react'

export function SignalGroup({
  id,
  title,
  count,
  helper,
  emphasis,
  children,
}: {
  id?: string
  title: string
  count: number
  helper?: string
  emphasis?: boolean
  children: React.ReactNode
}) {
  return (
    <section id={id} className="mb-[var(--px-group-gap)] scroll-mt-4">
      <div className="flex items-baseline gap-2.5 px-1 pb-2.5">
        <h2
          className="m-0 text-[14px] font-bold"
          style={{ color: 'var(--px-fg)', letterSpacing: '-0.1px' }}
        >
          {title}
        </h2>
        <span
          className="px-mono text-[11px]"
          style={{ color: 'var(--px-fg-4)', fontVariantNumeric: 'tabular-nums' }}
        >
          {count}
        </span>
        {helper && (
          <span
            className="text-[11.5px] italic"
            style={{ color: 'var(--px-fg-4)' }}
          >
            · {helper}
          </span>
        )}
      </div>
      <div
        className="overflow-hidden rounded-[10px] border"
        style={{
          background: 'var(--px-surface)',
          borderColor: emphasis ? 'var(--px-hairline-strong)' : 'var(--px-hairline)',
          boxShadow: emphasis ? 'var(--px-shadow-sm)' : 'none',
        }}
      >
        {children}
      </div>
    </section>
  )
}
