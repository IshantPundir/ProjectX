'use client'

import { useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'

import { Button } from '@/components/ui/button'
import { UnifiedPipelineView } from '@/components/dashboard/pipeline/UnifiedPipelineView'
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
    return <div className="text-sm text-zinc-500">Loading pipeline…</div>
  }

  if (!job.can_manage || job.status !== 'signals_confirmed') {
    return (
      <div className="max-w-4xl">
        <Link
          href={`/jobs/${jobId}`}
          className="text-sm text-zinc-500 hover:text-zinc-900 mb-1 inline-block"
        >
          ← Back to job
        </Link>
        <p className="text-sm text-zinc-500 mt-4">
          This pipeline is not available for editing.
        </p>
      </div>
    )
  }

  if (!pipeline) {
    return (
      <div className="max-w-4xl">
        <Link
          href={`/jobs/${jobId}`}
          className="text-sm text-zinc-500 hover:text-zinc-900 mb-1 inline-block"
        >
          ← Back to job
        </Link>
        <h1 className="text-2xl font-semibold text-zinc-900 mb-2">
          No pipeline yet
        </h1>
        <p className="text-sm text-zinc-500 mb-6">
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

  return (
    <UnifiedPipelineView
      key={pipeline.id}
      job={job}
      pipeline={pipeline}
      jobId={jobId}
    />
  )
}
