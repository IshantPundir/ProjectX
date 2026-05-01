'use client'

import { useMemo } from 'react'

import type { ActivationPredicateFailure } from '@/lib/api/pipelines'
import { computeActivationFailures } from '@/lib/pipelines/activation'
import { useBanksOverview } from '@/lib/hooks/use-banks-overview'
import { useJob } from '@/lib/hooks/use-job'
import { useJobPipeline } from '@/lib/hooks/use-job-pipeline'

/** All inputs required to render the layout-level activation banner. */
export type ActivationState =
  | { kind: 'loading' }
  /** Pre-pipeline states the banner doesn't render for: draft, signals_extracting,
   *  signals_extraction_failed, archived. The relevant page surfaces handle these. */
  | { kind: 'hidden' }
  /** signals_extracted — recruiter has reviewed the JD; banner nudges to lock signals. */
  | { kind: 'signals_extracted' }
  /** signals_confirmed | pipeline_built — render the predicate failures. */
  | {
      kind: 'pipeline_review'
      failures: ActivationPredicateFailure[]
      ready: boolean
      /** Banks currently in `generating` (one entry per stage). PR 3 surfaces
       *  this as a live "⏳ Generating" state in the banner. */
      generatingStageIds: string[]
    }
  /** Job already activated. Banner stays hidden — the green "live" chip is
   *  enough; layering a banner would just be visual noise. */
  | { kind: 'active' }

/**
 * One source of truth for the activation banner across all job tabs.
 *
 * Wraps `useJob`, `useJobPipeline`, and `useBanksOverview`, runs the same
 * activation predicates the Pipeline tab's in-funnel banner used to compute,
 * and surfaces a tagged-union shape so the consumer renders the right
 * variant declaratively (no nested null-check gymnastics).
 *
 * Caching: every input goes through TanStack Query, so the banner re-renders
 * automatically when the worker publishes `bank.status_changed` and the
 * caller invalidates `['banks-overview', jobId]` (which the SSE stream hook
 * already does).
 */
export function useActivationState(jobId: string): ActivationState {
  const { data: job, isLoading: jobLoading } = useJob(jobId)
  // Pipeline + banks queries are gated on a status that has a pipeline
  // (signals_confirmed onwards) — this avoids 404s and unnecessary network
  // for jobs that are still in the JD review phase.
  const hasPipeline = !!job && (
    job.status === 'signals_confirmed'
    || job.status === 'pipeline_built'
    || job.status === 'active'
  )
  const { data: pipeline, isLoading: pipelineLoading } = useJobPipeline(
    hasPipeline ? jobId : '',
  )
  const { data: banksOverview, isLoading: banksLoading } = useBanksOverview(
    hasPipeline ? jobId : '',
  )

  return useMemo<ActivationState>(() => {
    if (jobLoading || !job) return { kind: 'loading' }

    // Pre-pipeline + terminal states: banner is hidden. The page-level
    // surfaces (extract-in-progress spinner, extraction error banner) own
    // those experiences.
    if (
      job.status === 'draft'
      || job.status === 'signals_extracting'
      || job.status === 'signals_extraction_failed'
      || job.status === 'archived'
    ) {
      return { kind: 'hidden' }
    }

    if (job.status === 'signals_extracted') {
      return { kind: 'signals_extracted' }
    }

    if (job.status === 'active') {
      return { kind: 'active' }
    }

    // signals_confirmed | pipeline_built: load pipeline + banks
    if (pipelineLoading || banksLoading) return { kind: 'loading' }
    if (!pipeline || !banksOverview) return { kind: 'loading' }

    // The backend's `list_banks` endpoint returns mixed real banks and
    // placeholder entries (`status === "not_generated"`). The frontend
    // `BankResponse` type does not currently model the placeholder case,
    // so we narrow at runtime. Anything that isn't a real bank (no
    // `generated_at`) gets filtered out — the activation predicate's
    // missing_bank rule will fire for those stages anyway.
    const realBanks: { stage_id: string; status: string }[] = banksOverview.banks
      .filter((b) => (b as { status: string }).status !== 'not_generated')
      .map((b) => ({ stage_id: b.stage_id, status: b.status as string }))
    const failures = computeActivationFailures(pipeline, realBanks)
    const generatingStageIds = realBanks
      .filter((b) => b.status === 'generating')
      .map((b) => b.stage_id)
    return {
      kind: 'pipeline_review',
      failures,
      ready: failures.length === 0,
      generatingStageIds,
    }
  }, [job, jobLoading, pipeline, pipelineLoading, banksOverview, banksLoading])
}
