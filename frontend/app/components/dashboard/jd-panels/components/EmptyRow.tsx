'use client'

export function EmptyRow({ label }: { label: string }) {
  return (
    <div
      className="px-3.5 py-3 text-[12px] italic"
      style={{ color: 'var(--px-fg-4)' }}
    >
      {label}
    </div>
  )
}
