'use client'

import type { JobPostingWithSnapshot } from '@/lib/api/jobs'

// Local copy of the page-level icon helper. The original `I` lives in
// page.tsx and will move with its primary consumer in a later task; until
// then, this canvas gets its own minimal copy + just the icon path it needs.
function I({
  d,
  size = 14,
  stroke = 1.6,
}: {
  d: string | readonly string[]
  size?: number
  stroke?: number
}) {
  const paths = Array.isArray(d) ? d : [d as string]
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flexShrink: 0 }}
      aria-hidden="true"
    >
      {paths.map((p, i) => (
        <path key={i} d={p} />
      ))}
    </svg>
  )
}

const REFRESH_ICON = 'M21 12a9 9 0 11-3-6.7L21 8M21 3v5h-5'

export function EnrichedJdCanvas({
  job,
  onReEnrich,
}: {
  job: JobPostingWithSnapshot
  onReEnrich: () => void
}) {
  const text = job.description_enriched

  return (
    <main
      className="flex min-w-0 flex-col overflow-hidden rounded-[10px] border"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div className="flex-shrink-0 px-6 pb-4 pt-5">
        <h1
          className="m-0 text-[22px] font-semibold"
          style={{ color: 'var(--px-fg)', letterSpacing: '-0.4px' }}
        >
          Enriched JD
        </h1>
        <div className="mt-1 text-[12.5px]" style={{ color: 'var(--px-fg-3)' }}>
          Rewritten by Copilot to match your company voice.
        </div>
      </div>

      <div
        className="flex h-10 flex-shrink-0 items-center gap-1.5 border-b px-6"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <div className="flex-1" />
        <button
          type="button"
          className="px-btn ghost xs"
          onClick={onReEnrich}
          disabled={job.is_confirmed}
        >
          <I d={REFRESH_ICON} size={10} />
          Re-enrich
        </button>
      </div>

      <div className="px-6 pb-8 pt-5">
        <article
          className="rounded-[10px] border p-6"
          style={{
            background: 'var(--px-surface)',
            borderColor: 'var(--px-hairline)',
          }}
        >
          <pre
            className="px-serif m-0 whitespace-pre-wrap text-[14px]"
            style={{
              color: 'var(--px-fg-2)',
              lineHeight: 1.65,
              fontFamily: 'var(--font-serif)',
            }}
          >
            {text || 'No content.'}
          </pre>
        </article>

        {job.project_scope_raw && (
          <article
            className="mt-4 rounded-[10px] border p-6"
            style={{
              background: 'var(--px-surface)',
              borderColor: 'var(--px-hairline)',
            }}
          >
            <div className="px-eyebrow mb-2">Project scope</div>
            <pre
              className="m-0 whitespace-pre-wrap text-[13px]"
              style={{
                color: 'var(--px-fg-2)',
                lineHeight: 1.6,
                fontFamily: 'var(--font-sans)',
              }}
            >
              {job.project_scope_raw}
            </pre>
          </article>
        )}
      </div>
    </main>
  )
}
