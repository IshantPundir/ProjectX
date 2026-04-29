'use client'

import { useRouter } from 'next/navigation'
import Link from 'next/link'

import { useActivateJob } from '@/lib/hooks/use-activate-job'
import { useActivationState } from '@/lib/hooks/use-activation-state'
import { useQuestionsStatusStream } from '@/lib/hooks/use-questions-status-stream'
import type { ActivationPredicateFailure } from '@/lib/api/pipelines'

type Tab = 'jd' | 'pipeline' | 'questions'

type Props = {
  jobId: string
  tab: Tab
}

/**
 * Layout-level activation banner shown above the job title on every tab.
 *
 * Adapts content to the current job state and the active tab:
 *   - signals_extracted: nudge to lock the signals (links to the existing
 *     "Confirm signals" button on the JD tab via the #confirm-signals-cta
 *     anchor).
 *   - signals_confirmed | pipeline_built: render the activation-predicate
 *     failures, with the "Activate" button enabled when failures is empty.
 *   - active / archived / pre-pipeline states: hidden (the page-level
 *     surfaces own those experiences and the status pill conveys "live").
 *
 * Source of truth: `useActivationState(jobId)` — single hook that wraps
 * useJob + useJobPipeline + useBanksOverview and runs the same predicates
 * that used to live on the Pipeline tab's in-funnel ActivationGate.
 */
export function JobActivationBanner({ jobId, tab }: Props) {
  const state = useActivationState(jobId)
  const activateMutation = useActivateJob(jobId)
  const router = useRouter()

  // Open the SSE stream whenever the banner is mounted on a job that has
  // a pipeline. The stream invalidates `['banks', jobId]` on every event,
  // so `useActivationState` re-renders the banner with fresh
  // generatingStageIds + failures within ~100ms of any bank state change
  // (driven by the actor publishes we wired in earlier sessions).
  // The hook is gated on `jobId` being non-empty so other states don't
  // open a stream they won't use.
  const streamJobId = state.kind === 'pipeline_review' ? jobId : ''
  useQuestionsStatusStream(streamJobId, null)

  if (state.kind === 'loading' || state.kind === 'hidden' || state.kind === 'active') {
    return null
  }

  if (state.kind === 'signals_extracted') {
    return <SignalsExtractedBanner jobId={jobId} tab={tab} />
  }

  // pipeline_review
  return (
    <PipelineReviewBanner
      jobId={jobId}
      failures={state.failures}
      ready={state.ready}
      generatingStageIds={state.generatingStageIds}
      onActivate={() => activateMutation.mutate()}
      activating={activateMutation.isPending}
      router={router}
    />
  )
}

/* ─── Variants ─────────────────────────────────────────────────────────── */

function SignalsExtractedBanner({
  jobId,
  tab,
}: {
  jobId: string
  tab: Tab
}) {
  // On the JD tab, scroll to the existing Confirm-signals button. On any
  // other tab, navigate to the JD tab first; the browser will honour the
  // hash on arrival.
  const href = tab === 'jd' ? '#confirm-signals-cta' : `/jobs/${jobId}#confirm-signals-cta`

  return (
    <BannerShell tone="amber">
      <div className="flex flex-1 items-start gap-3">
        <span className="text-zinc-900 font-medium">
          Lock the signals to start building your pipeline.
        </span>
      </div>
      <Link
        href={href}
        onClick={(e) => {
          // Same-tab link with a hash — Link doesn't scroll on its own when
          // already on the page. Force the scroll into view so the user
          // doesn't wonder where they were taken.
          if (tab === 'jd') {
            e.preventDefault()
            document
              .getElementById('confirm-signals-cta')
              ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
          }
        }}
        className="rounded bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800"
      >
        Lock signals
      </Link>
    </BannerShell>
  )
}

function PipelineReviewBanner({
  jobId,
  failures,
  ready,
  generatingStageIds,
  onActivate,
  activating,
  router,
}: {
  jobId: string
  failures: ActivationPredicateFailure[]
  ready: boolean
  generatingStageIds: string[]
  onActivate: () => void
  activating: boolean
  router: ReturnType<typeof useRouter>
}) {
  // While a stage's bank is mid-generation, suppress its "Generate a question
  // bank for 'X'" failure — the bank IS being generated, so the message is
  // misleading. We surface the in-flight state separately as a top-line
  // ⏳ note. Non-bank failures (missing interviewer, empty stage name, etc.)
  // pass through unchanged because the recruiter can act on them in parallel.
  const generatingSet = new Set(generatingStageIds)
  const visibleFailures = failures.filter(
    (f) =>
      !(
        f.code === 'missing_bank'
        && f.stage_id
        && generatingSet.has(f.stage_id)
      ),
  )
  const isGenerating = generatingStageIds.length > 0
  const visibleReady = visibleFailures.length === 0 && !isGenerating

  let headline: string
  if (isGenerating) {
    const n = generatingStageIds.length
    headline = `⏳ Generating questions for ${n} stage${n === 1 ? '' : 's'}…`
  } else if (visibleReady) {
    headline = '✓ Ready to activate this job. Candidates will be able to enter the pipeline.'
  } else {
    headline = `⚠ ${visibleFailures.length} thing${visibleFailures.length === 1 ? '' : 's'} needed before you can activate this job:`
  }

  // Stage focus from any tab: deep-link the Pipeline tab with `?stage=<id>`.
  // The Pipeline funnel reads that param on mount, opens the stage's config
  // sheet, and clears the param. Single mechanism regardless of which tab
  // the user clicked from — keeps the banner stateless.
  const handleFailureClick = (f: ActivationPredicateFailure) => {
    if (!f.stage_id) return
    router.push(`/jobs/${jobId}/pipeline?stage=${encodeURIComponent(f.stage_id)}`)
  }
  // The Activate button is enabled ONLY when there are no failures AND no
  // generation is in flight. Server-side activation rejects pipelines whose
  // banks aren't in reviewing/confirmed; a bank in 'generating' would 422.
  const activateEnabled = ready && !isGenerating

  // Tone: emerald only when fully ready; sky-blue while generating
  // (signals progress, not blockage); amber for outstanding failures.
  const tone: 'amber' | 'emerald' | 'sky' = visibleReady
    ? 'emerald'
    : isGenerating
      ? 'sky'
      : 'amber'

  return (
    <BannerShell tone={tone}>
      <div className="space-y-2 flex-1">
        <p className="font-medium text-zinc-900">{headline}</p>
        {isGenerating && (
          <p className="text-xs text-zinc-600">
            Question banks typically take 2–3 minutes per stage. The page will
            update automatically when each stage finishes.
          </p>
        )}
        {!visibleReady && visibleFailures.length > 0 && (
          <ul className="space-y-1 text-sm">
            {visibleFailures.map((f, i) => (
              <li key={`${f.code}-${f.stage_id ?? i}`}>
                {f.stage_id ? (
                  <button
                    type="button"
                    onClick={() => handleFailureClick(f)}
                    className="text-left text-zinc-700 underline-offset-2 hover:underline"
                  >
                    • {f.message}
                  </button>
                ) : (
                  <span className="text-zinc-700">• {f.message}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      <button
        type="button"
        disabled={!activateEnabled || activating}
        onClick={onActivate}
        className="rounded bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
      >
        {activating ? 'Activating…' : 'Activate'}
      </button>
    </BannerShell>
  )
}

/* ─── Shared shell ─────────────────────────────────────────────────────── */

function BannerShell({
  tone,
  children,
}: {
  tone: 'amber' | 'emerald' | 'sky'
  children: React.ReactNode
}) {
  // Three tones for three meanings:
  //   amber   — outstanding configuration work blocks activation
  //   sky     — work is in flight (LLM generation), not actionable yet
  //   emerald — ready to activate
  const cls =
    tone === 'emerald'
      ? 'rounded-lg border border-emerald-300 bg-emerald-50 p-4'
      : tone === 'sky'
        ? 'rounded-lg border border-sky-300 bg-sky-50 p-4'
        : 'rounded-lg border border-amber-300 bg-amber-50 p-4'
  return (
    <div className={cls}>
      <div className="flex items-start justify-between gap-4">{children}</div>
    </div>
  )
}
