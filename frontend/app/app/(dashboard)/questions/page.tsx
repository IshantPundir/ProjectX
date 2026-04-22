'use client'

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'

import { Button } from '@/components/px'
import { QuestionsMainPane } from '@/components/dashboard/question-bank/QuestionsMainPane'
import { jobsApi, type JobPostingSummary } from '@/lib/api/jobs'
import { useJobPipeline } from '@/lib/hooks/use-job-pipeline'
import { useBanksOverview } from '@/lib/hooks/use-banks-overview'
import type { PipelineStageResponse } from '@/lib/api/pipelines'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/**
 * Top-level Question bank surface — v4 "power-user" entry point.
 *
 * Question banks are per-stage, so this page walks the user through:
 *   1. pick a role (signals_confirmed + has pipeline)
 *   2. pick a stage from that role's pipeline
 *   3. edit the stage's bank using the existing <QuestionsMainPane>
 *
 * Both choices live in the URL (`?role=<jobId>&stage=<stageId>`) for
 * deep-linking. Clicking the breadcrumb back-links clears the downstream
 * parameter so navigation is unambiguous.
 */
export default function QuestionsIndexPage() {
  const router = useRouter()
  const params = useSearchParams()
  const selectedRoleId = params.get('role')
  const selectedStageId = params.get('stage')

  const { data: jobs, isLoading: jobsLoading } = useQuery<JobPostingSummary[]>({
    queryKey: ['jobs-list'],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.list(token)
    },
  })

  const eligibleJobs = useMemo(
    () => (jobs ?? []).filter((j) => j.status === 'signals_confirmed'),
    [jobs],
  )

  function update(patch: Record<string, string | null>) {
    const next = new URLSearchParams(params.toString())
    for (const [k, v] of Object.entries(patch)) {
      if (v === null) next.delete(k)
      else next.set(k, v)
    }
    const qs = next.toString()
    router.replace(`/questions${qs ? `?${qs}` : ''}`, { scroll: false })
  }

  const selectedJob = useMemo(
    () => eligibleJobs.find((j) => j.id === selectedRoleId) ?? null,
    [eligibleJobs, selectedRoleId],
  )

  return (
    <div className="mx-auto max-w-[1400px] px-8 pb-10 pt-5">
      {/* Breadcrumb / back chain */}
      <div
        className="mb-2 flex items-center gap-2 text-[12px]"
        style={{ color: 'var(--px-fg-3)' }}
      >
        {selectedRoleId && (
          <>
            <button
              type="button"
              onClick={() => update({ role: null, stage: null })}
              className="inline-flex cursor-pointer items-center gap-1.5 transition-colors"
              style={{ color: 'var(--px-fg-3)' }}
            >
              <svg
                width="12"
                height="12"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
              </svg>
              All roles
            </button>
            {selectedStageId && selectedJob && (
              <>
                <span style={{ color: 'var(--px-fg-5)' }}>/</span>
                <button
                  type="button"
                  onClick={() => update({ stage: null })}
                  className="cursor-pointer hover:underline"
                  style={{ color: 'var(--px-fg-3)' }}
                >
                  {selectedJob.title}
                </button>
              </>
            )}
          </>
        )}
      </div>

      {/* Title */}
      <div className="mb-5">
        <h1
          className="px-serif m-0 text-[30px] font-normal"
          style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
        >
          Question bank
        </h1>
        <p
          className="mt-1 text-[12.5px]"
          style={{ color: 'var(--px-fg-3)' }}
        >
          {!selectedRoleId
            ? 'Review and edit the interview questions for any live role.'
            : !selectedStageId
              ? 'Pick the stage to edit its question bank.'
              : 'Edit the question bank for this stage.'}
        </p>
      </div>

      {!selectedRoleId ? (
        <RolePicker
          jobs={eligibleJobs}
          loading={jobsLoading}
          onPick={(id) => update({ role: id, stage: null })}
        />
      ) : !selectedStageId ? (
        <StagePickerForRole
          jobId={selectedRoleId}
          onPick={(stageId) => update({ stage: stageId })}
        />
      ) : (
        <QuestionsMainPane
          jobId={selectedRoleId}
          stageId={selectedStageId}
        />
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
          Question banks are per-stage inside a role&apos;s pipeline. Create a
          role and configure its pipeline first.
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
              gridTemplateColumns: 'minmax(0,2.4fr) 120px 120px',
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
              className="text-right text-[12px]"
              style={{ color: 'var(--px-accent)' }}
            >
              Pick stage →
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

/* ─── Stage picker ────────────────────────────────────────── */

function StagePickerForRole({
  jobId,
  onPick,
}: {
  jobId: string
  onPick: (stageId: string) => void
}) {
  const { data: pipeline, isLoading: pipelineLoading } = useJobPipeline(jobId)
  const { data: overview, isLoading: banksLoading } = useBanksOverview(jobId)

  if (pipelineLoading || banksLoading) {
    return (
      <div className="text-sm" style={{ color: 'var(--px-fg-3)' }}>
        Loading pipeline…
      </div>
    )
  }

  if (!pipeline) {
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
          This role has no pipeline yet
        </h2>
        <p className="mb-4 text-sm" style={{ color: 'var(--px-fg-3)' }}>
          Configure the interview pipeline first — each stage gets its own
          question bank.
        </p>
        <Link href={`/pipeline?role=${jobId}`}>
          <Button size="sm">Configure pipeline →</Button>
        </Link>
      </div>
    )
  }

  const stages = pipeline.stages
  const banksByStage = new Map(
    (overview?.banks ?? []).map((b) => [b.stage_id, b]),
  )

  return (
    <div>
      <div
        className="mb-3 text-[10.5px] font-semibold uppercase"
        style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
      >
        Pick a stage
      </div>
      <div
        className="overflow-hidden rounded-[10px] border"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        {stages.map((stage: PipelineStageResponse, i: number) => {
          const bank = banksByStage.get(stage.id)
          return (
            <button
              key={stage.id}
              type="button"
              onClick={() => onPick(stage.id)}
              className="grid w-full cursor-pointer items-center gap-4 text-left text-[12.5px] transition-colors hover:brightness-[0.99]"
              style={{
                gridTemplateColumns: '32px minmax(0,2.4fr) 140px 90px 120px',
                padding: '14px 18px',
                borderBottom:
                  i < stages.length - 1
                    ? '1px solid var(--px-hairline)'
                    : 'none',
                background: 'transparent',
              }}
            >
              <div
                className="px-mono text-[12px]"
                style={{
                  color: 'var(--px-fg-4)',
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {stage.position + 1}
              </div>
              <div className="min-w-0">
                <div
                  className="truncate text-[13.5px] font-medium"
                  style={{ color: 'var(--px-fg)' }}
                >
                  {stage.name}
                </div>
                <div
                  className="mt-0.5 text-[11.5px]"
                  style={{ color: 'var(--px-fg-4)' }}
                >
                  {stage.stage_type.replace(/_/g, ' ')} ·{' '}
                  {stage.duration_minutes} min
                </div>
              </div>
              <div>
                <BankStatusPill status={bank?.status ?? null} />
              </div>
              <div
                className="px-mono text-right text-[12px]"
                style={{
                  color: 'var(--px-fg-2)',
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {bank?.question_count ? `${bank.question_count} Qs` : '—'}
              </div>
              <div
                className="text-right text-[12px]"
                style={{ color: 'var(--px-accent)' }}
              >
                Open →
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function BankStatusPill({
  status,
}: {
  status: 'draft' | 'generating' | 'reviewing' | 'confirmed' | 'failed' | null
}) {
  const map = {
    draft: { label: 'No bank', color: 'var(--px-fg-4)' },
    generating: { label: 'Generating…', color: 'var(--px-caution)' },
    reviewing: { label: 'Review', color: 'var(--px-accent)' },
    confirmed: { label: 'Confirmed', color: 'var(--px-ok)' },
    failed: { label: 'Failed', color: 'var(--px-danger)' },
  } as const
  const v = status ? map[status] : { label: 'No bank', color: 'var(--px-fg-4)' }
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2 text-[10.5px] font-medium"
      style={{
        height: 20,
        borderColor: 'var(--px-hairline)',
        color: v.color,
      }}
    >
      <span
        className="h-[5px] w-[5px] rounded-full"
        style={{ background: v.color }}
      />
      {v.label}
    </span>
  )
}
