'use client'

import { useEffect } from 'react'
import { useParams, useRouter, useSearchParams } from 'next/navigation'

import { EnrichedJdPanel } from '@/components/dashboard/jd-panels/EnrichedJdPanel'
import { ErrorBanner } from '@/components/dashboard/jd-panels/ErrorBanner'
import { LoadingSkeleton } from '@/components/dashboard/jd-panels/LoadingSkeleton'
import { OriginalJdPanel } from '@/components/dashboard/jd-panels/OriginalJdPanel'
import { SignalsPanelWrapper } from '@/components/dashboard/jd-panels/SignalsPanelWrapper'
import { StaleBanner } from '@/components/dashboard/jd-panels/StaleBanner'
import { useJob } from '@/lib/hooks/use-job'
import { useJobPipeline } from '@/lib/hooks/use-job-pipeline'
import { useJobStatusStream } from '@/lib/hooks/use-job-status-stream'
import { useTriggerEnrich } from '@/lib/hooks/use-trigger-enrich'

export default function JobReviewPage() {
  const params = useParams<{ jobId: string }>()
  const jobId = params.jobId
  const searchParams = useSearchParams()
  const router = useRouter()

  const { data: job, isLoading } = useJob(jobId)
  const { data: pipeline } = useJobPipeline(jobId)
  const { status, error: sseError } = useJobStatusStream(jobId)
  const triggerEnrich = useTriggerEnrich(jobId)

  // Redirect to Pipeline tab if pipeline exists and user didn't explicitly
  // request the JD tab via ?tab=jd (which the tab link in the layout sets).
  useEffect(() => {
    if (!pipeline) return
    if (searchParams.get('tab') === 'jd') return
    router.replace(`/jobs/${jobId}/pipeline`)
  }, [pipeline, searchParams, router, jobId])

  if (isLoading || !job) {
    return <LoadingSkeleton status={status} sseError={sseError} />
  }

  const showSkeleton =
    job.status === 'draft' || job.status === 'signals_extracting'
  const showError = job.status === 'signals_extraction_failed'
  const showPanels =
    (job.status === 'signals_extracted' || job.status === 'signals_confirmed') &&
    job.latest_snapshot &&
    job.description_enriched

  const isStale =
    job.enrichment_status !== 'completed' &&
    job.enrichment_status !== 'streaming' &&
    job.latest_snapshot !== null

  const isEnriching = job.enrichment_status === 'streaming'
  const enrichmentError =
    job.enrichment_status === 'failed' ? (job.enrichment_error ?? 'Unknown error') : null

  return (
    <div>
      {showSkeleton && <LoadingSkeleton status={status} sseError={sseError} />}

      {showError && (
        <ErrorBanner jobId={jobId} error={job.status_error} />
      )}

      {showPanels && job.latest_snapshot && job.description_enriched && (
        <div className="grid grid-cols-[auto_1fr_minmax(280px,320px)] 3xl:grid-cols-[1fr_2fr_1.2fr] gap-4 min-h-[70vh]">
          <OriginalJdPanel
            descriptionRaw={job.description_raw}
            projectScopeRaw={job.project_scope_raw}
          />
          <EnrichedJdPanel
            enrichedJd={job.description_enriched}
            banner={
              <StaleBanner
                isStale={isStale}
                isEnriching={isEnriching}
                enrichmentError={enrichmentError}
                onReEnrich={() => triggerEnrich.mutate()}
                onRetry={() => triggerEnrich.mutate()}
              />
            }
          />
          <SignalsPanelWrapper
            snapshot={job.latest_snapshot}
            isConfirmed={job.is_confirmed}
            canManage={job.can_manage}
            jobId={jobId}
          />
        </div>
      )}
    </div>
  )
}
