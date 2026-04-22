'use client'

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { Button } from '@/components/px'
import { TemplatePickerDialog } from '@/components/dashboard/pipeline/TemplatePickerDialog'
import { UnifiedPipelineView } from '@/components/dashboard/pipeline/UnifiedPipelineView'
import { jobsApi, type JobPostingSummary } from '@/lib/api/jobs'
import { useCreateJobPipeline } from '@/lib/hooks/use-create-job-pipeline'
import { useJob } from '@/lib/hooks/use-job'
import { useJobPipeline } from '@/lib/hooks/use-job-pipeline'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/**
 * Top-level Pipeline surface.
 *
 * Pipelines are per-role — this page is a picker that lets a recruiter
 * jump straight into a specific role's pipeline builder without walking
 * through the Roles list. The `?role=<jobId>` query param keeps the
 * choice shareable + deep-linkable. Role selection is only offered for
 * jobs with `status === 'signals_confirmed'` (the same guard the
 * per-job pipeline page enforces).
 */
export default function PipelineIndexPage() {
  const router = useRouter()
  const params = useSearchParams()
  const selectedId = params.get('role')

  const { data: jobs, isLoading: jobsLoading } = useQuery<JobPostingSummary[]>({
    queryKey: ['jobs-list'],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.list(token)
    },
  })

  // Only jobs whose signals are confirmed can own a pipeline.
  const eligibleJobs = useMemo(
    () => (jobs ?? []).filter((j) => j.status === 'signals_confirmed'),
    [jobs],
  )

  function selectRole(id: string) {
    const next = new URLSearchParams(params.toString())
    next.set('role', id)
    router.replace(`/pipeline?${next.toString()}`, { scroll: false })
  }

  function clearRole() {
    const next = new URLSearchParams(params.toString())
    next.delete('role')
    const qs = next.toString()
    router.replace(`/pipeline${qs ? `?${qs}` : ''}`, { scroll: false })
  }

  return (
    <div className="mx-auto max-w-[1400px] px-8 pb-10 pt-5">
      {/* Header */}
      <div className="mb-5">
        {selectedId && (
          <button
            type="button"
            onClick={clearRole}
            className="mb-2 inline-flex cursor-pointer items-center gap-1.5 text-[12px] transition-colors"
            style={{ color: 'var(--px-fg-3)' }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
            </svg>
            All roles
          </button>
        )}
        <h1
          className="px-serif m-0 text-[30px] font-normal"
          style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
        >
          Pipeline
        </h1>
        <p
          className="mt-1 text-[12.5px]"
          style={{ color: 'var(--px-fg-3)' }}
        >
          {selectedId
            ? 'Configure the interview stages for this role.'
            : 'Configure the interview flow for any live role.'}
        </p>
      </div>

      {!selectedId ? (
        <RolePicker
          jobs={eligibleJobs}
          loading={jobsLoading}
          onPick={selectRole}
        />
      ) : (
        <PipelineForRole jobId={selectedId} />
      )}
    </div>
  )
}

/* ─── Role picker ─────────────────────────────────────────── */

function RolePicker({
  jobs,
  loading,
  onPick,
}: {
  jobs: JobPostingSummary[]
  loading: boolean
  onPick: (id: string) => void
}) {
  if (loading) {
    return (
      <div className="text-sm" style={{ color: 'var(--px-fg-3)' }}>
        Loading roles…
      </div>
    )
  }

  if (jobs.length === 0) {
    return (
      <div
        className="rounded-[10px] border p-12 text-center"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <h2
          className="px-serif m-0 mb-2 text-xl"
          style={{ color: 'var(--px-fg)' }}
        >
          No live roles yet
        </h2>
        <p
          className="mx-auto mb-6 max-w-md text-sm"
          style={{ color: 'var(--px-fg-3)' }}
        >
          Pipelines are per-role. Create a role and confirm its signals first,
          then come back to configure the interview flow.
        </p>
        <Link href="/jobs/new">
          <Button size="sm">+ New role</Button>
        </Link>
      </div>
    )
  }

  return (
    <div>
      <div
        className="mb-3 text-[10.5px] font-semibold uppercase"
        style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
      >
        Pick a role
      </div>
      <div
        className="overflow-hidden rounded-[10px] border"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        {jobs.map((job, i) => (
          <button
            key={job.id}
            type="button"
            onClick={() => onPick(job.id)}
            className="grid w-full cursor-pointer items-center gap-4 text-left text-[12.5px] transition-colors hover:brightness-[0.99]"
            style={{
              gridTemplateColumns: 'minmax(0,2.4fr) 120px 90px 120px',
              padding: '14px 18px',
              borderBottom:
                i < jobs.length - 1
                  ? '1px solid var(--px-hairline)'
                  : 'none',
              background: 'transparent',
            }}
          >
            <div className="min-w-0">
              <div
                className="truncate text-[13.5px] font-medium"
                style={{ color: 'var(--px-fg)' }}
              >
                {job.title}
              </div>
              <div
                className="mt-0.5 truncate text-[11.5px]"
                style={{ color: 'var(--px-fg-4)' }}
              >
                {job.org_unit_name ?? '—'}
              </div>
            </div>
            <span
              className="inline-flex items-center gap-1.5 rounded-full border px-2 text-[10.5px] font-medium"
              style={{
                height: 20,
                borderColor: 'var(--px-hairline)',
                color: 'var(--px-ok)',
              }}
            >
              <span
                className="h-[5px] w-[5px] rounded-full"
                style={{ background: 'var(--px-ok)' }}
              />
              Live
            </span>
            <div
              className="px-mono text-right text-[12px]"
              style={{
                color: 'var(--px-fg-2)',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {job.signal_count > 0 ? `${job.signal_count} sig` : '—'}
            </div>
            <div
              className="text-right text-[12px]"
              style={{ color: 'var(--px-accent)' }}
            >
              Configure →
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

/* ─── Pipeline view for selected role ─────────────────────── */

function PipelineForRole({ jobId }: { jobId: string }) {
  const { data: job, isLoading: jobLoading } = useJob(jobId)
  const { data: pipeline, isLoading: pipelineLoading } = useJobPipeline(jobId)
  const createMutation = useCreateJobPipeline(jobId)
  const [pickerOpen, setPickerOpen] = useState(false)

  if (jobLoading || pipelineLoading) {
    return (
      <div className="text-sm" style={{ color: 'var(--px-fg-3)' }}>
        Loading pipeline…
      </div>
    )
  }

  if (!job) {
    return (
      <div className="text-sm" style={{ color: 'var(--px-danger)' }}>
        Role not found or not accessible.
      </div>
    )
  }

  if (!job.can_manage || job.status !== 'signals_confirmed') {
    return (
      <div
        className="rounded-[10px] border p-8"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <h2
          className="px-serif m-0 mb-2 text-xl"
          style={{ color: 'var(--px-fg)' }}
        >
          This pipeline isn&apos;t editable yet
        </h2>
        <p className="mb-4 text-sm" style={{ color: 'var(--px-fg-3)' }}>
          The role&apos;s signals need to be confirmed before the pipeline can
          be configured.
        </p>
        <Link href={`/jobs/${jobId}`}>
          <Button size="sm" variant="outline">
            Go to role → review signals
          </Button>
        </Link>
      </div>
    )
  }

  if (!pipeline) {
    return (
      <>
        <div
          className="rounded-[10px] border p-8"
          style={{
            background: 'var(--px-surface)',
            borderColor: 'var(--px-hairline)',
          }}
        >
          <h2
            className="px-serif m-0 mb-2 text-xl"
            style={{ color: 'var(--px-fg)' }}
          >
            {job.title} has no pipeline yet
          </h2>
          <p className="mb-6 text-sm" style={{ color: 'var(--px-fg-3)' }}>
            Pick a template from your library, the starter pack, or build from
            scratch.
          </p>
          <Button
            size="sm"
            onClick={() => setPickerOpen(true)}
            disabled={createMutation.isPending}
          >
            Pick a pipeline
          </Button>
        </div>
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
      </>
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
