export default function ReportLoading() {
  return (
    <div className="mx-auto max-w-[1400px] px-6 pb-10 pt-5">
      <div className="mb-4 h-8 w-72 animate-pulse rounded" style={{ background: 'var(--px-surface-2)' }} />
      <div className="grid grid-cols-1 gap-3.5 xl:grid-cols-[1.85fr_1fr]">
        <div className="space-y-3.5">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="animate-pulse rounded-xl border" style={{ height: i === 0 ? 220 : 160, background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }} />
          ))}
        </div>
        <div className="space-y-3.5">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="animate-pulse rounded-xl border" style={{ height: 180, background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }} />
          ))}
        </div>
      </div>
    </div>
  )
}
