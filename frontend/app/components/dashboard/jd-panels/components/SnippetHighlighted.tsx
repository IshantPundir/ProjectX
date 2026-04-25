'use client'

export function SnippetHighlighted({ text, needle }: { text: string; needle: string }) {
  const i = text.toLowerCase().indexOf(needle.toLowerCase())
  if (i < 0) return <span>{text}</span>
  const before = text.slice(0, i)
  const match = text.slice(i, i + needle.length)
  const after = text.slice(i + needle.length)
  return (
    <>
      <span style={{ color: 'var(--px-fg-4)' }}>{before}</span>
      <span
        style={{
          background: 'var(--px-accent-tint)',
          color: 'var(--px-accent)',
          padding: '1px 4px',
          borderRadius: 3,
          fontWeight: 500,
        }}
      >
        {match}
      </span>
      <span style={{ color: 'var(--px-fg-4)' }}>{after}</span>
    </>
  )
}
