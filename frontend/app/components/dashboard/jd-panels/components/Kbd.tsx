'use client'

export function Kbd({ keys }: { keys: readonly string[] }) {
  return (
    <span className="inline-flex gap-0.5">
      {keys.map((k, i) => (
        <span key={i} className="px-kbd">
          {k}
        </span>
      ))}
    </span>
  )
}
