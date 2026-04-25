'use client'

export function InspectorTips() {
  return (
    <aside
      className="sticky self-start flex flex-col overflow-y-auto rounded-[10px] border px-4 py-5"
      style={{
        // 48px AppShell top bar + 12px gap = 60
        top: 60,
        maxHeight: 'calc(100vh - 72px)',
        background: 'var(--px-bg-2)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div className="px-eyebrow mb-3">Reading the JD</div>
      <div
        className="text-[13px]"
        style={{ color: 'var(--px-fg-2)', lineHeight: 1.6 }}
      >
        The enriched version is what candidates would see if you published
        today. Flip to <b>Raw</b> to compare against what you originally
        pasted.
      </div>
      <div
        className="my-4 h-px"
        style={{ background: 'var(--px-hairline)' }}
      />
      <div
        className="text-[12.5px]"
        style={{ color: 'var(--px-fg-3)', lineHeight: 1.6 }}
      >
        Switch back to <b>Signals</b> on the left when you&apos;re ready to
        review extracted signals.
      </div>
    </aside>
  )
}
