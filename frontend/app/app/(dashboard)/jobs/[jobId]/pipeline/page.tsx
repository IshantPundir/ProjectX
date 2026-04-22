'use client'

import { useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'

import { Button } from '@/components/px'
import { JobPipelineFunnel } from '@/components/dashboard/pipeline/JobPipelineFunnel'
import { TemplatePickerDialog } from '@/components/dashboard/pipeline/TemplatePickerDialog'
import { useJob } from '@/lib/hooks/use-job'
import { useJobPipeline } from '@/lib/hooks/use-job-pipeline'
import { useCreateJobPipeline } from '@/lib/hooks/use-create-job-pipeline'

export default function JobPipelinePage() {
  const params = useParams<{ jobId: string }>()
  const jobId = params.jobId

  const { data: job, isLoading: jobLoading } = useJob(jobId)
  const { data: pipeline, isLoading: pipelineLoading } = useJobPipeline(jobId)
  const createMutation = useCreateJobPipeline(jobId)

  const [pickerOpen, setPickerOpen] = useState(false)

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

  if (!pipeline) {
    return (
      <div className="max-w-4xl">
        <h2
          className="px-serif m-0 mb-2 text-[24px] font-normal"
          style={{ letterSpacing: '-0.5px', color: 'var(--px-fg)' }}
        >
          No pipeline yet
        </h2>
        <p className="mb-6 text-sm" style={{ color: 'var(--px-fg-3)' }}>
          Pick a template from your library, the starter pack, or build from
          scratch.
        </p>
        <Button
          onClick={() => setPickerOpen(true)}
          disabled={createMutation.isPending}
        >
          Pick a pipeline
        </Button>
        {pickerOpen && (
          <TemplatePickerDialog
            orgUnitId={job.org_unit_id}
            open={pickerOpen}
            onClose={() => setPickerOpen(false)}
            onPickTemplate={(t) =>
              createMutation.mutate(
                { source: 'template', template_id: t.id },
                { onSuccess: () => setPickerOpen(false) },
              )
            }
            onPickStarter={(s) =>
              createMutation.mutate(
                { source: 'starter', starter_key: s.key },
                { onSuccess: () => setPickerOpen(false) },
              )
            }
          />
        )}
      </div>
    )
  }

  return <JobPipelineFunnel key={pipeline.id} job={job} pipeline={pipeline} jobId={jobId} />
}
