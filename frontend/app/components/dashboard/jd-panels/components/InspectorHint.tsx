'use client'

export function InspectorHint({
  needsReviewCount,
  isConfirmed,
}: {
  needsReviewCount: number
  isConfirmed: boolean
}) {
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
      <div className="px-eyebrow mb-3">Copilot</div>
      <div
        className="text-[13px]"
        style={{ color: 'var(--px-fg-2)', lineHeight: 1.6 }}
      >
        {isConfirmed ? (
          <>
            These signals are live. Any change here republishes to candidates
            in flight, so tread carefully.
          </>
        ) : needsReviewCount > 0 ? (
          <>
            I flagged <b>{needsReviewCount}</b> signal
            {needsReviewCount === 1 ? '' : 's'} as worth a second look. Click
            any row to see my reasoning and adjust.
          </>
        ) : (
          <>
            Signals look solid. Click any row to see where it came from in the
            JD and the questions I&apos;d ask around it.
          </>
        )}
      </div>

      <div
        className="my-4 h-px"
        style={{ background: 'var(--px-hairline)' }}
      />

      <div className="px-eyebrow mb-3">Tips</div>
      <ul
        className="m-0 flex flex-col gap-2 pl-4 text-[12.5px]"
        style={{ color: 'var(--px-fg-3)', lineHeight: 1.6 }}
      >
        <li>
          Click any signal to see &ldquo;where in the JD&rdquo; it came from.
        </li>
        <li>
          <span className="px-kbd">⌘</span>
          <span className="px-kbd">↵</span> approves &amp; publishes.
        </li>
        <li>Nothing auto-publishes — you approve the final version.</li>
      </ul>
    </aside>
  )
}
