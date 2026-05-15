// Streaming server-render fallback for /tracker. The client page renders its
// own in-tree skeleton too — this kicks in only during the initial pre-hydration
// phase, so a recruiter never sees a blank screen.
export default function TrackerLoading() {
  return (
    <div className="mx-auto max-w-[1400px] px-8 pb-10 pt-[22px]">
      <div className="mb-5">
        <div
          className="h-9 w-40 animate-pulse rounded"
          style={{ background: 'var(--px-surface-2)' }}
        />
        <div
          className="mt-2 h-3.5 w-80 animate-pulse rounded"
          style={{ background: 'var(--px-surface-2)' }}
        />
      </div>
      <div
        className="grid gap-3"
        style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))' }}
      >
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="animate-pulse rounded-[10px] border"
            style={{
              height: 180,
              background: 'var(--px-surface)',
              borderColor: 'var(--px-hairline)',
            }}
          />
        ))}
      </div>
    </div>
  )
}
