'use client'

import type { ReactNode } from 'react'

type Props = {
  enrichedJd: string
  banner?: ReactNode
}

export function EnrichedJdPanel({ enrichedJd, banner }: Props) {
  return (
    <section
      className="col-span-1 min-w-0 overflow-auto rounded-[10px] border p-6"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <h3
        className="mb-4 border-b pb-2 text-[11px] font-semibold uppercase"
        style={{
          letterSpacing: '1.1px',
          color: 'var(--px-fg-4)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        Enriched JD
      </h3>
      {banner && <div className="mb-4">{banner}</div>}
      <div
        className="whitespace-pre-wrap text-[14px]"
        style={{ color: 'var(--px-fg-2)', lineHeight: 1.65 }}
      >
        {enrichedJd}
      </div>
    </section>
  )
}
