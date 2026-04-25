'use client'

import { useEffect } from 'react'
import { useParams, useRouter, useSearchParams } from 'next/navigation'

import { ErrorBanner } from '@/components/dashboard/jd-panels/ErrorBanner'
import { JDReviewShell } from '@/components/dashboard/jd-panels'
import { LoadingSkeleton } from '@/components/dashboard/jd-panels/LoadingSkeleton'
import { useJob } from '@/lib/hooks/use-job'
import { useJobPipeline } from '@/lib/hooks/use-job-pipeline'
import { useJobStatusStream } from '@/lib/hooks/use-job-status-stream'
import { useTriggerEnrich } from '@/lib/hooks/use-trigger-enrich'

export default function JobReviewPage() {
  const params = useParams<{ jobId: string }>()
  const jobId = params.jobId
  const searchParams = useSearchParams()
  const router = useRouter()

  const { status, error: sseError, isStreaming } = useJobStatusStream(jobId)
  const { data: job, isLoading } = useJob(jobId, isStreaming)
  const { data: pipeline } = useJobPipeline(jobId)
  const triggerEnrich = useTriggerEnrich(jobId)

  // Preserve the prior redirect: once the pipeline exists AND the user
  // confirmed signals, the JD tab is read-only; nudge them to pipeline
  // unless they explicitly asked for the JD view with ?tab=jd.
  useEffect(() => {
    if (!pipeline) return
    if (job?.status !== 'signals_confirmed') return
    if (searchParams.get('tab') === 'jd') return
    router.replace(`/jobs/${jobId}/pipeline`)
  }, [pipeline, job?.status, searchParams, router, jobId])

  if (isLoading || !job) {
    return <LoadingSkeleton status={status} sseError={sseError} />
  }

  if (job.status === 'draft' || job.status === 'signals_extracting') {
    return <LoadingSkeleton status={status} sseError={sseError} />
  }

  if (job.status === 'signals_extraction_failed') {
    return <ErrorBanner jobId={jobId} error={job.status_error} />
  }

  if (!job.latest_snapshot) {
    return (
      <div
        className="rounded-[10px] border p-8 text-sm"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
          color: 'var(--px-fg-3)',
        }}
      >
        No signals snapshot yet.
      </div>
    )
  }

  // Remount the shell when the snapshot version changes so useState-based
  // draft signals reset cleanly — avoids setState-in-effect churn.
  return (
    <JDReviewShell
      key={job.latest_snapshot.version}
      job={job}
      onReEnrich={() => triggerEnrich.mutate()}
    />
  )
}
