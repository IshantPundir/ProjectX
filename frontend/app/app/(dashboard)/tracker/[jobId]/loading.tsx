// Streaming server-render fallback while the client component hydrates.
export default function TrackerBoardLoading() {
  return (
    <div className="mx-auto max-w-[1600px] px-8 pb-10 pt-5">
      <div
        className="mb-3 h-8 w-72 animate-pulse rounded"
        style={{ background: 'var(--px-surface-2)' }}
      />
      <div
        className="mb-4 h-3 w-96 animate-pulse rounded"
        style={{ background: 'var(--px-surface-2)' }}
      />
      <div className="flex gap-2.5 overflow-x-auto pb-4">
        {Array.from({ length: 5 }).map((_, i) => (
          <div
            key={i}
            className="flex w-80 flex-shrink-0 flex-col rounded-lg border"
            style={{
              minHeight: 320,
              background: 'var(--px-bg-2)',
              borderColor: 'var(--px-hairline)',
            }}
          >
            <div
              className="m-3 h-4 w-24 animate-pulse rounded"
              style={{ background: 'var(--px-surface-2)' }}
            />
          </div>
        ))}
      </div>
    </div>
  )
}
