'use client'

export default function ReportError({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="mx-auto max-w-[800px] px-8 pt-12 text-center">
      <h2 className="px-serif text-2xl" style={{ color: 'var(--px-fg)' }}>Couldn&rsquo;t load this report</h2>
      <p className="mx-auto mt-2 max-w-md text-sm" style={{ color: 'var(--px-fg-3)' }}>
        {error.message || 'Something went wrong.'}
      </p>
      <button type="button" onClick={reset} className="px-btn primary sm mt-6 inline-block">Try again</button>
    </div>
  )
}
