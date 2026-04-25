'use client'

export function Confidence({ value, inline = false }: { value: number; inline?: boolean }) {
  const filled = Math.round(value * 10)
  const color =
    value >= 0.75
      ? 'var(--px-ok)'
      : value >= 0.5
        ? 'var(--px-caution)'
        : 'var(--px-danger)'
  return (
    <span className="inline-flex items-center gap-0.5">
      <span className="px-conf-bar inline-flex h-3 items-center gap-[2px]">
        {Array.from({ length: 10 }).map((_, i) => (
          <span
            key={i}
            style={{
              width: 3,
              height: 3 + (i % 3),
              background: i < filled ? color : 'var(--px-surface-3)',
              borderRadius: 1,
            }}
          />
        ))}
      </span>
      {!inline && (
        <span
          className="px-mono ml-1.5 text-[10.5px]"
          style={{
            color: 'var(--px-fg-3)',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {Math.round(value * 100)}%
        </span>
      )}
    </span>
  )
}
