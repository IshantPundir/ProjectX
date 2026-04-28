'use client'

import type { JobPostingWithSnapshot } from '@/lib/api/jobs'

/**
 * Renders the original raw JD verbatim (description_raw). No formatting,
 * no markdown — preserved as-is so the user sees what they pasted.
 */
export function RawJdCanvas({ job }: { job: JobPostingWithSnapshot }) {
  return (
    <section
      className="rounded-[10px] border p-6 max-w-none"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
        color: 'var(--px-fg)',
      }}
    >
      <h3
        className="px-eyebrow mb-4"
        style={{ marginBottom: 14 }}
      >
        Raw JD
      </h3>
      <div
        className="text-[13.5px] whitespace-pre-wrap"
        style={{ color: 'var(--px-fg-2)', lineHeight: 1.65 }}
      >
        {job.description_raw}
      </div>
    </section>
  )
}
