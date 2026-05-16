'use client'

import { useParams, usePathname } from 'next/navigation'
import Link from 'next/link'

import { JobActivationBanner } from '@/components/dashboard/job/JobActivationBanner'
import { useJob } from '@/lib/hooks/use-job'
import type { JobStatus } from '@/lib/api/jobs'

type TabId = 'jd' | 'pipeline' | 'questions'

type TabDef = {
  id: TabId
  label: string
  href: string
  active: boolean
  disabled?: boolean
  disabledReason?: string
  badge?: number
}

/* ─── Status chip — mirrors the design's "Ready to publish" / "live" chips ─── */

/**
 * Canonical job-status chip. Single source of truth for status vocabulary
 * across the recruiter dashboard — jobs list, tracker landing, tracker
 * cards all consume the same label table. Color story: gray = work in
 * flight, amber = needs your attention, green = running, red = error.
 *
 * Lifecycle vocabulary (spec
 * docs/superpowers/specs/2026-05-15-job-activation-gate-design.md):
 *   draft → Reading JD → Review signals → In review → Live
 *
 * "In review" covers both signals_confirmed and pipeline_built —
 * signals_confirmed is transient now that confirm_signals auto-creates
 * the bookend pipeline and advances to pipeline_built in the same
 * transaction.
 */
function JobStatusChips({ status, signalCount }: { status: JobStatus; signalCount: number }) {
  if (status === 'draft') {
    return (
      <span className="px-chip soft" style={{ height: 22 }}>
        <span className="px-dot" />
        Draft
      </span>
    )
  }
  if (status === 'signals_extracting') {
    return (
      <span className="px-chip ai" style={{ height: 22 }}>
        <span className="px-dot px-pulse" />
        Reading JD
      </span>
    )
  }
  if (status === 'signals_extraction_failed') {
    return (
      <span className="px-chip danger" style={{ height: 22 }}>
        <span className="px-dot" />
        Extraction failed
      </span>
    )
  }
  if (status === 'signals_extracted') {
    return (
      <>
        <span className="px-chip caution" style={{ height: 22 }}>
          <span className="px-dot" />
          Review signals
        </span>
        {signalCount > 0 && (
          <span
            className="px-chip soft"
            style={{ height: 22 }}
            title={`${signalCount} signals extracted`}
          >
            {signalCount} signals
          </span>
        )}
      </>
    )
  }
  if (status === 'signals_confirmed' || status === 'pipeline_built') {
    return (
      <span className="px-chip soft" style={{ height: 22 }}>
        <span className="px-dot" />
        In review
      </span>
    )
  }
  if (status === 'active') {
    return (
      <span className="px-chip ok" style={{ height: 22 }}>
        <span className="px-dot" />
        Live · accepting candidates
      </span>
    )
  }
  if (status === 'archived') {
    return (
      <span className="px-chip soft" style={{ height: 22 }}>
        <span className="px-dot" />
        Archived
      </span>
    )
  }
  // Defensive fallback for any future status — render the raw value
  // rather than mis-labelling the job.
  return (
    <span className="px-chip soft" style={{ height: 22 }}>
      <span className="px-dot" />
      {status}
    </span>
  )
}

export default function JobLayout({ children }: { children: React.ReactNode }) {
  const params = useParams<{ jobId: string }>()
  const jobId = params.jobId
  const pathname = usePathname()

  const { data: job, isLoading } = useJob(jobId)

  const isPipelineRoute = pathname.startsWith(`/jobs/${jobId}/pipeline`)
  const isQuestionsRoute = pathname.startsWith(`/jobs/${jobId}/questions`)

  const pipelineEnabled =
    job?.status === 'signals_confirmed' ||
    job?.status === 'pipeline_built' ||
    job?.status === 'active'
  const questionsEnabled =
    job?.status === 'signals_confirmed' ||
    job?.status === 'pipeline_built' ||
    job?.status === 'active'

  const tabs: TabDef[] = [
    {
      id: 'jd',
      label: 'Job description',
      href: `/jobs/${jobId}?tab=jd`,
      active: !isPipelineRoute && !isQuestionsRoute,
    },
    {
      id: 'pipeline',
      label: 'Pipeline',
      href: `/jobs/${jobId}/pipeline`,
      active: isPipelineRoute,
      disabled: !pipelineEnabled,
      disabledReason: pipelineEnabled ? undefined : 'Confirm signals first',
    },
    {
      id: 'questions',
      label: 'Interview questions',
      href: `/jobs/${jobId}/questions`,
      active: isQuestionsRoute,
      disabled: !questionsEnabled,
      disabledReason: questionsEnabled ? undefined : 'Confirm signals first',
    },
  ]

  /* ─── Metadata line — department · location · comp · level ─── */
  const metaParts: string[] = []
  if (job) {
    if (job.org_unit_name) metaParts.push(job.org_unit_name)
    if (job.location) metaParts.push(job.location)
    if (job.work_arrangement && job.work_arrangement !== 'onsite') {
      metaParts.push(job.work_arrangement === 'remote' ? 'Remote' : 'Hybrid')
    }
    if (job.salary_range_min && job.salary_range_max) {
      const cur = job.salary_currency ?? ''
      metaParts.push(
        `${cur} ${job.salary_range_min.toLocaleString()}–${job.salary_range_max.toLocaleString()}`,
      )
    }
    if (job.latest_snapshot?.seniority_level) {
      metaParts.push(
        job.latest_snapshot.seniority_level.charAt(0).toUpperCase() +
          job.latest_snapshot.seniority_level.slice(1),
      )
    }
  }

  // Tab the banner is being rendered on. Drives tab-aware copy in the
  // banner (e.g. JD tab links the "Lock signals" CTA via #anchor; other
  // tabs navigate to the JD page first).
  const currentTab: 'jd' | 'pipeline' | 'questions' = isPipelineRoute
    ? 'pipeline'
    : isQuestionsRoute
      ? 'questions'
      : 'jd'

  return (
    <div className="mx-auto max-w-[1400px] px-8 pb-10 pt-[22px]">
      {/* Shared header */}
      <div className="mb-5">
        <Link
          href="/jobs"
          className="mb-2 inline-block text-[12px] hover:underline"
          style={{ color: 'var(--px-fg-3)' }}
        >
          ← Roles
        </Link>

        {/* Layout-level activation banner. Hidden when state.kind is
            'loading' / 'hidden' / 'active' — the banner returns null and
            takes no vertical space, so the title still renders flush
            against the breadcrumb. */}
        {job && (
          <div className="mb-4">
            <JobActivationBanner jobId={job.id} tab={currentTab} />
          </div>
        )}
        {isLoading || !job ? (
          <div
            className="mt-1 h-9 w-72 animate-pulse rounded"
            style={{ background: 'var(--px-surface-2)' }}
          />
        ) : (
          <>
            <div className="flex items-baseline gap-2.5 flex-wrap">
              <h1
                className="px-serif m-0 text-[28px] font-normal"
                style={{ letterSpacing: '-0.5px', color: 'var(--px-fg)' }}
              >
                {job.title}
              </h1>
              <JobStatusChips status={job.status} signalCount={job.signal_count} />
            </div>
            {metaParts.length > 0 && (
              <div
                className="mt-1.5 flex flex-wrap gap-2 text-[12.5px]"
                style={{ color: 'var(--px-fg-3)' }}
              >
                {metaParts.map((p, i) => (
                  <span key={i} className="flex items-center gap-2">
                    {i > 0 && <span style={{ color: 'var(--px-fg-4)' }}>·</span>}
                    <span>{p}</span>
                  </span>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {/* Tab bar */}
      <div
        className="mb-6 border-b"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        <nav className="-mb-px flex gap-0" aria-label="Job sections">
          {tabs.map((tab) => (
            <TabLink key={tab.id} {...tab} />
          ))}
        </nav>
      </div>

      {/* Tab content */}
      {children}
    </div>
  )
}

function TabLink({
  href,
  label,
  active,
  disabled,
  disabledReason,
  badge,
}: TabDef) {
  const base =
    'px-4 py-2.5 text-[13px] font-medium border-b-2 transition-colors duration-200 inline-flex items-center gap-1.5'

  if (disabled) {
    return (
      <span
        className={`${base} cursor-not-allowed`}
        style={{ color: 'var(--px-fg-5)', borderColor: 'transparent' }}
        title={disabledReason}
        aria-disabled="true"
      >
        {label}
      </span>
    )
  }

  return (
    <Link
      href={href}
      className={base}
      style={{
        color: active ? 'var(--px-fg)' : 'var(--px-fg-3)',
        borderColor: active ? 'var(--px-accent)' : 'transparent',
      }}
      aria-current={active ? 'page' : undefined}
    >
      {label}
      {badge != null && (
        <span
          className="px-mono text-[10.5px]"
          style={{ color: 'var(--px-fg-4)', fontVariantNumeric: 'tabular-nums' }}
        >
          {badge}
        </span>
      )}
    </Link>
  )
}
