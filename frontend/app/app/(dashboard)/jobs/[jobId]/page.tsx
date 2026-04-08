'use client'

import { useParams } from 'next/navigation'

import { EnrichedJdPanel } from '@/components/dashboard/jd-panels/EnrichedJdPanel'
import { ErrorBanner } from '@/components/dashboard/jd-panels/ErrorBanner'
import { LoadingSkeleton } from '@/components/dashboard/jd-panels/LoadingSkeleton'
import { OriginalJdPanel } from '@/components/dashboard/jd-panels/OriginalJdPanel'
import { SignalsPanel } from '@/components/dashboard/jd-panels/SignalsPanel'
import { useJob } from '@/lib/hooks/use-job'
import { useJobStatusStream } from '@/lib/hooks/use-job-status-stream'

export default function JobReviewPage() {
  const params = useParams<{ jobId: string }>()
  const jobId = params.jobId

  const { data: job, isLoading } = useJob(jobId)
  const status = useJobStatusStream(jobId)

  if (isLoading || !job) {
    return <LoadingSkeleton status={status} />
  }

  const showSkeleton =
    job.status === 'draft' || job.status === 'signals_extracting'
  const showError = job.status === 'signals_extraction_failed'
  const showPanels =
    job.status === 'signals_extracted' &&
    job.latest_snapshot &&
    job.description_enriched

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-zinc-900">{job.title}</h1>
      </div>

      {showSkeleton && <LoadingSkeleton status={status} />}

      {showError && (
        <ErrorBanner jobId={jobId} error={job.status_error} />
      )}

      {showPanels && job.latest_snapshot && job.description_enriched && (
        <div className="grid grid-cols-[auto_1fr] 3xl:grid-cols-[1fr_2fr_1.2fr] gap-4 min-h-[70vh]">
          <OriginalJdPanel
            descriptionRaw={job.description_raw}
            projectScopeRaw={job.project_scope_raw}
          />
          <EnrichedJdPanel enrichedJd={job.description_enriched} />
          <SignalsPanel snapshot={job.latest_snapshot} />
        </div>
      )}
    </div>
  )
}
