'use client'

import type { JobPostingWithSnapshot } from '@/lib/api/jobs'

// Local copy of the page-level icon helper. The original `I` lives in
// page.tsx and will move with its primary consumer in a later task; until
// then, this header gets its own minimal copy + just the icon path it needs.
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

const WARN_ICON =
  'M10.3 3.9L2.7 17a2 2 0 001.7 3h15.2a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0zM12 9v4M12 17h.01'

export function CanvasHeader({
  job,
  needsReviewCount,
  isConfirmed,
}: {
  job: JobPostingWithSnapshot
  needsReviewCount: number
  isConfirmed: boolean
}) {
  const chips = isConfirmed ? (
    <>
      <span className="px-chip ok" style={{ height: 22 }}>
        <span className="px-dot" />
        live · accepting candidates
      </span>
    </>
  ) : needsReviewCount > 0 ? (
    <>
      <span className="px-chip ok" style={{ height: 22 }}>
        <span className="px-dot" />
        Ready to review
      </span>
      <span className="px-chip caution" style={{ height: 22 }}>
        <I d={WARN_ICON} size={10} />
        {needsReviewCount} to double-check
      </span>
    </>
  ) : (
    <span className="px-chip ok" style={{ height: 22 }}>
      <span className="px-dot" />
      Ready to publish
    </span>
  )

  const metaParts: string[] = []
  if (job.org_unit_name) metaParts.push(job.org_unit_name)
  if (job.location) metaParts.push(job.location)
  if (job.work_arrangement && job.work_arrangement !== 'onsite') {
    metaParts.push(job.work_arrangement === 'remote' ? 'Remote' : 'Hybrid 3d/wk')
  }
  if (job.salary_range_min && job.salary_range_max) {
    metaParts.push(
      `${job.salary_currency ?? ''} ${job.salary_range_min.toLocaleString()}–${job.salary_range_max.toLocaleString()}`,
    )
  }
  if (job.latest_snapshot?.seniority_level) {
    metaParts.push(
      job.latest_snapshot.seniority_level.charAt(0).toUpperCase() +
        job.latest_snapshot.seniority_level.slice(1),
    )
  }

  return (
    <div className="flex-shrink-0 px-6 pb-4 pt-5">
      <div className="mb-2 flex flex-wrap items-baseline gap-2.5">
        <h1
          className="m-0 text-[22px] font-semibold"
          style={{ color: 'var(--px-fg)', letterSpacing: '-0.4px' }}
        >
          What we found
        </h1>
        {chips}
      </div>
      {metaParts.length > 0 && (
        <div
          className="flex flex-wrap gap-2 text-[12.5px]"
          style={{ color: 'var(--px-fg-3)' }}
        >
          {metaParts.map((p, i) => (
            <span key={`${i}-${p}`} className="flex items-center gap-2">
              {i > 0 && <span style={{ color: 'var(--px-fg-4)' }}>·</span>}
              <span>{p}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
