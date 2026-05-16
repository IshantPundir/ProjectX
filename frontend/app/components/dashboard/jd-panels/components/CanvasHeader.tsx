'use client'

import type { JobPostingWithSnapshot } from '@/lib/api/jobs'

/**
 * Canvas-level header for the signals panel. The status chip that used
 * to live here was removed — the page layout (`JobLayout::JobStatusChips`)
 * already renders the canonical status chip above this canvas. Rendering
 * a second chip here previously caused "Almost ready" (layout) and "live
 * · accepting candidates" (canvas) to appear together on the same page,
 * contradicting each other.
 */
export function CanvasHeader({
  job,
}: {
  job: JobPostingWithSnapshot
}) {
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
