'use client'

import { useParams, usePathname } from 'next/navigation'
import Link from 'next/link'

import { useJob } from '@/lib/hooks/use-job'

type TabDef = {
  id: 'jd' | 'pipeline'
  label: string
  href: string
  active: boolean
  disabled?: boolean
  disabledReason?: string
}

export default function JobLayout({ children }: { children: React.ReactNode }) {
  const params = useParams<{ jobId: string }>()
  const jobId = params.jobId
  const pathname = usePathname()

  const { data: job, isLoading } = useJob(jobId)

  // Determine the active tab from pathname.
  // /jobs/{id}                -> JD tab
  // /jobs/{id}/pipeline       -> Pipeline tab
  // /jobs/{id}/pipeline/...   -> Pipeline tab
  // /jobs/{id}/questions      -> Pipeline tab (redirects to /pipeline)
  const isPipelineRoute =
    pathname.startsWith(`/jobs/${jobId}/pipeline`) ||
    pathname.startsWith(`/jobs/${jobId}/questions`)

  // Pipeline tab is enabled once signals are confirmed.
  const pipelineEnabled = job?.status === 'signals_confirmed'

  const tabs: TabDef[] = [
    {
      id: 'jd',
      label: 'Job Description',
      href: `/jobs/${jobId}?tab=jd`,
      active: !isPipelineRoute,
    },
    {
      id: 'pipeline',
      label: 'Pipeline',
      href: `/jobs/${jobId}/pipeline`,
      active: isPipelineRoute,
      disabled: !pipelineEnabled,
      disabledReason: pipelineEnabled ? undefined : 'Confirm signals first',
    },
  ]

  return (
    <div>
      {/* Shared header */}
      <div className="mb-5">
        <Link
          href="/jobs"
          className="text-sm text-zinc-500 hover:text-zinc-900 mb-1 inline-block"
        >
          ← Job Descriptions
        </Link>
        {isLoading || !job ? (
          <div className="h-8 w-64 bg-zinc-100 rounded animate-pulse mt-1" />
        ) : (
          <h1 className="text-2xl font-semibold text-zinc-900">{job.title}</h1>
        )}
      </div>

      {/* Tab bar */}
      <div className="border-b border-zinc-200 mb-6">
        <nav className="flex gap-0 -mb-px" aria-label="Job sections">
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

function TabLink({ href, label, active, disabled, disabledReason }: TabDef) {
  const base =
    'px-4 py-2.5 text-sm font-medium border-b-2 transition-colors duration-200 inline-flex items-center'

  if (disabled) {
    return (
      <span
        className={`${base} text-zinc-300 border-transparent cursor-not-allowed`}
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
      className={`${base} ${
        active
          ? 'text-zinc-900 border-blue-600'
          : 'text-zinc-500 border-transparent hover:text-zinc-900 hover:border-zinc-300'
      }`}
      aria-current={active ? 'page' : undefined}
    >
      {label}
    </Link>
  )
}
