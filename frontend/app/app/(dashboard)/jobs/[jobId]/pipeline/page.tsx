'use client'

import { useParams } from 'next/navigation'
import Link from 'next/link'

import { Button } from '@/components/px'
import { JobPipelineFunnel } from '@/components/dashboard/pipeline/JobPipelineFunnel'
import { PipelineSourcePicker } from '@/components/dashboard/pipeline/PipelineSourcePicker'
import { useJob } from '@/lib/hooks/use-job'
import { useJobPipeline } from '@/lib/hooks/use-job-pipeline'
import { useCreateJobPipeline } from '@/lib/hooks/use-create-job-pipeline'

export default function JobPipelinePage() {
  const params = useParams<{ jobId: string }>()
  const jobId = params.jobId

  const { data: job, isLoading: jobLoading } = useJob(jobId)
  const { data: pipeline, isLoading: pipelineLoading } = useJobPipeline(jobId)
  const createMutation = useCreateJobPipeline(jobId)

  if (jobLoading || pipelineLoading || !job) {
    return (
      <div className="text-sm" style={{ color: 'var(--px-fg-3)' }}>
        Loading pipeline…
      </div>
    )
  }

  if (!job.can_manage || job.status !== 'signals_confirmed') {
    return (
      <div className="max-w-4xl">
        <p className="mt-4 text-sm" style={{ color: 'var(--px-fg-3)' }}>
          This pipeline is not available for editing.{' '}
          {job.status !== 'signals_confirmed' &&
            'Confirm the role signals first to unlock pipeline editing.'}
        </p>
        <Link href={`/jobs/${jobId}?tab=jd`}>
          <Button size="sm" variant="outline" className="mt-3">
            ← Back to JD review
          </Button>
        </Link>
      </div>
    )
  }

  // No pipeline yet → show the source picker
  if (!pipeline) {
    return (
      <PipelineSourcePicker
        jobId={jobId}
        recentTemplates={[]}
        teamDefault={null}
        onPick={(body) => createMutation.mutate(body)}
      />
    )
  }

  // Pipeline exists → show the funnel
  return <JobPipelineFunnel key={pipeline.id} job={job} pipeline={pipeline} jobId={jobId} />
}
