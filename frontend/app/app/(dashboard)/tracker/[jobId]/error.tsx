'use client'

import Link from 'next/link'

export default function TrackerBoardError({
  error,
  reset,
}: {
  error: Error
  reset: () => void
}) {
  return (
    <div className="mx-auto max-w-[800px] px-8 pt-12 text-center">
      <h2
        className="px-serif text-2xl"
        style={{ color: 'var(--px-fg)' }}
      >
        Tracker board hit an unexpected error
      </h2>
      <p
        className="mx-auto mt-2 max-w-md text-sm"
        style={{ color: 'var(--px-fg-3)' }}
      >
        {error.message || 'Something went wrong while loading this board.'}
      </p>
      <div className="mt-6 flex items-center justify-center gap-2">
        <button
          type="button"
          onClick={reset}
          className="px-btn primary sm inline-block"
        >
          Try again
        </button>
        <Link
          href="/tracker"
          className="px-btn ghost sm inline-block"
        >
          ← Back to Tracker
        </Link>
      </div>
    </div>
  )
}
