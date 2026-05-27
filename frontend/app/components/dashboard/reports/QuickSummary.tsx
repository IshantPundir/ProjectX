export function QuickSummary({ text }: { text: string }) {
  if (!text) return null
  return (
    <section className="rounded-xl border bg-white p-4" style={{ borderColor: 'var(--px-hairline)' }} aria-label="Summary">
      <h2 className="mb-2 text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--px-fg-3)' }}>Quick summary</h2>
      <p className="whitespace-pre-wrap text-[13.5px] leading-relaxed" style={{ color: 'var(--px-fg-2)' }}>{text}</p>
    </section>
  )
}
