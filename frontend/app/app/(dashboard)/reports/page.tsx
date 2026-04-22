'use client'

/**
 * Reports — placeholder surface.
 *
 * Reports is listed as a top-level nav item in the v4 design but has no
 * design artboard yet — the roadmap (hiring funnel analytics, interviewer
 * calibration, offer-accept trend) will ship alongside Phase 4B. This
 * page exists so the nav link resolves instead of 404'ing, and it frames
 * the intent for anyone clicking through.
 */
export default function ReportsPage() {
  const planned = [
    {
      title: 'Hiring funnel',
      body: 'Conversion between every stage per role, with 30/60/90-day trend lines.',
    },
    {
      title: 'Time-to-hire',
      body: 'Median days from first signal to offer accept, broken down by division and level.',
    },
    {
      title: 'Interviewer calibration',
      body: 'Signal score deltas per interviewer against the hiring-panel baseline.',
    },
    {
      title: 'Offer-accept rate',
      body: 'Accept / decline / negotiate outcomes by role and compensation band.',
    },
  ]

  return (
    <div className="mx-auto max-w-[1100px] px-8 pb-10 pt-5">
      <div className="mb-6">
        <div
          className="mb-1 text-[10.5px] font-semibold uppercase"
          style={{ letterSpacing: '1.1px', color: 'var(--px-accent)' }}
        >
          Coming soon
        </div>
        <h1
          className="px-serif m-0 text-[30px] font-normal"
          style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
        >
          Reports
        </h1>
        <p
          className="mt-2 max-w-xl text-[13px]"
          style={{ color: 'var(--px-fg-3)', lineHeight: 1.6 }}
        >
          A dashboard that answers the questions recruiters actually get asked
          at review time — how fast, how clean, how consistent. The four
          reports below ship in the next release. In the meantime, per-role
          data lives inside each role&apos;s detail page.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3.5 md:grid-cols-2">
        {planned.map((r) => (
          <div
            key={r.title}
            className="relative overflow-hidden rounded-[10px] border p-5"
            style={{
              background: 'var(--px-surface)',
              borderColor: 'var(--px-hairline)',
            }}
          >
            <span
              className="absolute bottom-0 left-0 top-0 w-[2px]"
              style={{ background: 'var(--px-accent-line)' }}
              aria-hidden="true"
            />
            <div
              className="mb-1.5 text-[10.5px] font-semibold uppercase"
              style={{
                letterSpacing: '1.1px',
                color: 'var(--px-fg-4)',
              }}
            >
              Planned
            </div>
            <div
              className="mb-2 text-[15px] font-semibold"
              style={{ color: 'var(--px-fg)' }}
            >
              {r.title}
            </div>
            <div
              className="text-[12.5px]"
              style={{ color: 'var(--px-fg-3)', lineHeight: 1.55 }}
            >
              {r.body}
            </div>
          </div>
        ))}
      </div>

      <div
        className="mt-6 rounded-[10px] border p-4 text-[12.5px]"
        style={{
          background: 'var(--px-accent-tint)',
          borderColor: 'var(--px-accent-line)',
          color: 'var(--px-accent-2)',
        }}
      >
        <b>Want a report shipped sooner?</b> Open an issue or ping the product
        team — the priorities here are user-driven.
      </div>
    </div>
  )
}
