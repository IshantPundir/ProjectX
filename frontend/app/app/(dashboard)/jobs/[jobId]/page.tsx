'use client'

import { useEffect, useMemo, useState } from 'react'
import { useParams, useRouter, useSearchParams } from 'next/navigation'

import { LoadingSkeleton } from '@/components/dashboard/jd-panels/LoadingSkeleton'
import { ErrorBanner } from '@/components/dashboard/jd-panels/ErrorBanner'
import { SectionsRail } from '@/components/dashboard/jd-panels/SectionsRail'
import { FullJdCanvas } from '@/components/dashboard/jd-panels/FullJdCanvas'
import { SignalsCanvas } from '@/components/dashboard/jd-panels/SignalsCanvas'
import { Confidence } from '@/components/dashboard/jd-panels/components/Confidence'
import { SourceBadge } from '@/components/dashboard/jd-panels/components/SourceBadge'
import { SnippetHighlighted } from '@/components/dashboard/jd-panels/components/SnippetHighlighted'
import { InspectorHint } from '@/components/dashboard/jd-panels/components/InspectorHint'
import { InspectorTips } from '@/components/dashboard/jd-panels/components/InspectorTips'
import { InspectorAction } from '@/components/dashboard/jd-panels/components/InspectorAction'
import { groupSignals } from '@/components/dashboard/jd-panels/helpers/groupSignals'
import { needsReview } from '@/components/dashboard/jd-panels/helpers/needsReview'
import { weightToConfidence } from '@/components/dashboard/jd-panels/helpers/weightToConfidence'
import { findSnippet } from '@/components/dashboard/jd-panels/helpers/findSnippet'
import { suggestQuestions } from '@/components/dashboard/jd-panels/helpers/suggestQuestions'
import { useJob } from '@/lib/hooks/use-job'
import { useJobPipeline } from '@/lib/hooks/use-job-pipeline'
import { useJobStatusStream } from '@/lib/hooks/use-job-status-stream'
import { useSaveSignals } from '@/lib/hooks/use-save-signals'
import { useConfirmSignals } from '@/lib/hooks/use-confirm-signals'
import { useTriggerEnrich } from '@/lib/hooks/use-trigger-enrich'
import type { JobPostingWithSnapshot, SignalItem } from '@/lib/api/jobs'

/* ─── Page ────────────────────────────────────────────────── */

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

/* ─── Three-panel shell ─────────────────────────────────── */

type InnerView = 'signals' | 'jd'

function JDReviewShell({
  job,
  onReEnrich,
}: {
  job: JobPostingWithSnapshot
  onReEnrich: () => void
}) {
  const searchParams = useSearchParams()
  const router = useRouter()

  const snapshot = job.latest_snapshot!
  const [signals, setSignals] = useState<SignalItem[]>(snapshot.signals)
  const [isDirty, setIsDirty] = useState(false)

  const view = (searchParams.get('view') ?? 'signals') as InnerView
  const focusIdxParam = searchParams.get('signal')
  const focusIdx = focusIdxParam ? Number(focusIdxParam) : null

  const setView = (v: InnerView) => {
    const qs = new URLSearchParams(searchParams.toString())
    if (v === 'signals') qs.delete('view')
    else qs.set('view', v)
    qs.set('tab', 'jd')
    router.replace(`/jobs/${job.id}?${qs.toString()}`, { scroll: false })
  }

  const setFocus = (idx: number | null) => {
    const qs = new URLSearchParams(searchParams.toString())
    if (idx === null) qs.delete('signal')
    else qs.set('signal', String(idx))
    qs.set('tab', 'jd')
    router.replace(`/jobs/${job.id}?${qs.toString()}`, { scroll: false })
  }

  const { must, nice } = useMemo(() => groupSignals(signals), [signals])

  const needsReviewCount = signals.filter(needsReview).length
  const totalCount = signals.length

  const [activeSection, setActiveSection] = useState<
    'must' | 'nice' | 'snapshot' | 'jd'
  >(must.length > 0 ? 'must' : nice.length > 0 ? 'nice' : 'snapshot')

  const saveMutation = useSaveSignals(job.id)
  const confirmMutation = useConfirmSignals(job.id)

  const updateSignal = (index: number, patch: Partial<SignalItem>) => {
    setSignals((prev) => prev.map((s, i) => (i === index ? { ...s, ...patch } : s)))
    setIsDirty(true)
  }
  const removeSignal = (index: number) => {
    setSignals((prev) => prev.filter((_, i) => i !== index))
    setIsDirty(true)
    setFocus(null)
  }

  const save = () => {
    saveMutation.mutate(
      {
        signals,
        seniority_level: snapshot.seniority_level,
        role_summary: snapshot.role_summary,
      },
      {
        onSuccess: () => setIsDirty(false),
      },
    )
  }

  const saveAndConfirm = () => {
    if (!isDirty) {
      confirmMutation.mutate()
      return
    }
    saveMutation.mutate(
      {
        signals,
        seniority_level: snapshot.seniority_level,
        role_summary: snapshot.role_summary,
      },
      {
        onSuccess: () => {
          setIsDirty(false)
          confirmMutation.mutate()
        },
      },
    )
  }

  const focusSignal = focusIdx != null ? signals[focusIdx] : null

  // Overall state chip
  const isConfirmed = job.is_confirmed
  const stateBanner = needsReviewCount >= 4 ? 'low-confidence' : null
  const canManage = job.can_manage

  return (
    // items-stretch (default) is load-bearing: sticky children inside a grid
    // pin within their grid cell. If the cell is sized to content (items-start),
    // there's no track for sticky to traverse — the panel just sits in flow.
    <div className="grid gap-3" style={{ gridTemplateColumns: '220px 1fr 380px' }}>
      <SectionsRail
        must={must}
        nice={nice}
        hasSnapshot={!!snapshot.role_summary || !!snapshot.seniority_level}
        totalCount={totalCount}
        needsReviewCount={needsReviewCount}
        activeSection={view === 'jd' ? 'jd' : activeSection}
        filename={`jd-v${snapshot.version}.txt`}
        onShowJd={() => {
          setView('jd')
          setActiveSection('jd')
        }}
        onJump={(target) => {
          if (target === 'jd') {
            setView('jd')
            setActiveSection('jd')
            return
          }

          const wasJd = view === 'jd'
          if (wasJd) setView('signals')
          setActiveSection(target)

          const run = () => {
            const elId =
              target === 'must'
                ? 'sig-group-must'
                : target === 'nice'
                  ? 'sig-group-nice'
                  : 'sig-group-snapshot'
            const el = document.getElementById(elId)
            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }

          if (wasJd) {
            requestAnimationFrame(() => requestAnimationFrame(run))
          } else {
            run()
          }
        }}
      />

      {view === 'jd' ? (
        <FullJdCanvas job={job} onReEnrich={onReEnrich} />
      ) : (
        <SignalsCanvas
          must={must}
          nice={nice}
          job={job}
          stateBanner={stateBanner}
          isConfirmed={isConfirmed}
          canManage={canManage}
          isDirty={isDirty}
          saving={saveMutation.isPending}
          confirming={confirmMutation.isPending}
          needsReviewCount={needsReviewCount}
          totalCount={totalCount}
          focusIdx={focusIdx}
          onFocus={setFocus}
          onSave={save}
          onSaveAndConfirm={saveAndConfirm}
          onReEnrich={onReEnrich}
        />
      )}

      {view === 'jd' ? (
        <InspectorTips />
      ) : focusSignal ? (
        <SignalInspector
          signal={focusSignal}
          signalIndex={focusIdx!}
          jobRaw={job.description_raw}
          canManage={canManage}
          onUpdate={(patch) => updateSignal(focusIdx!, patch)}
          onRemove={() => removeSignal(focusIdx!)}
        />
      ) : (
        <InspectorHint
          needsReviewCount={needsReviewCount}
          isConfirmed={isConfirmed}
        />
      )}
    </div>
  )
}

/* ─── Right inspector ────────────────────────────────────── */

function SignalInspector({
  signal,
  signalIndex,
  jobRaw,
  canManage,
  onUpdate,
  onRemove,
}: {
  signal: SignalItem
  signalIndex: number
  jobRaw: string
  canManage: boolean
  onUpdate: (patch: Partial<SignalItem>) => void
  onRemove: () => void
}) {
  const confidence = weightToConfidence(signal.weight)
  const confidenceLabel =
    confidence >= 0.75
      ? 'Looking solid'
      : confidence >= 0.5
        ? 'Worth a second look'
        : "Copilot wasn't sure"
  const confidenceColor =
    confidence >= 0.75
      ? 'var(--px-ok)'
      : confidence >= 0.5
        ? 'var(--px-caution)'
        : 'var(--px-danger)'

  // Best-effort snippet: search the raw JD for the signal value to show
  // "where this came from" — the design treats this as the hero receipt.
  const snippet = findSnippet(jobRaw, signal.value)

  const draftedQuestions: string[] = suggestQuestions(signal)

  return (
    <aside
      className="sticky self-start flex flex-col overflow-y-auto rounded-[10px] border"
      style={{
        // 48px AppShell top bar + 12px gap = 60
        top: 60,
        maxHeight: 'calc(100vh - 72px)',
        background: 'var(--px-bg-2)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div
        className="border-b px-4 py-4"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        <div className="mb-1.5 flex items-center gap-2">
          <SourceBadge kind={signal.source} />
          <span className="px-eyebrow">Signal #{signalIndex + 1}</span>
        </div>
        <div
          className="mb-0.5 text-[16px] font-semibold"
          style={{ color: 'var(--px-fg)', letterSpacing: '-0.2px' }}
        >
          {signal.value}
        </div>
        <div className="text-[12px]" style={{ color: 'var(--px-fg-3)' }}>
          {signal.priority === 'required' ? 'Must-have' : 'Nice-to-have'} ·{' '}
          {signal.source === 'ai_extracted'
            ? 'Copilot pulled this verbatim'
            : signal.source === 'ai_inferred'
              ? 'Copilot inferred this from context'
              : 'You added this'}
          {signal.knockout && ' · deal-breaker'}
        </div>
      </div>

      <div
        className="border-b px-4 py-3.5"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        <div className="px-eyebrow mb-2.5">How confident</div>
        <div className="mb-2.5 flex items-center gap-3">
          <Confidence value={confidence} />
          <span
            className="text-[12px] font-medium"
            style={{ color: confidenceColor }}
          >
            {confidenceLabel}
          </span>
        </div>
        <div
          className="text-[12.5px]"
          style={{ color: 'var(--px-fg-3)', lineHeight: 1.55 }}
        >
          {signal.inference_basis
            ? signal.inference_basis
            : confidence >= 0.75
              ? 'The JD calls this out explicitly, and it aligns with every similar role on your team.'
              : 'The JD is ambiguous on this one — I made a judgment call based on the seniority and role context.'}
        </div>
      </div>

      <div
        className="border-b px-4 py-3.5"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        <div className="px-eyebrow mb-2.5">Where in the JD</div>
        {snippet ? (
          <div
            className="rounded-md border p-3 text-[12.5px]"
            style={{
              background: 'var(--px-surface)',
              borderColor: 'var(--px-hairline)',
              lineHeight: 1.55,
              color: 'var(--px-fg-2)',
              fontFamily: 'var(--font-serif)',
            }}
          >
            <SnippetHighlighted text={snippet} needle={signal.value} />
          </div>
        ) : (
          <div
            className="rounded-md border p-3 text-[12.5px] italic"
            style={{
              background: 'var(--px-surface-2)',
              borderColor: 'var(--px-hairline)',
              color: 'var(--px-fg-4)',
            }}
          >
            Not a direct match in the JD — Copilot inferred this from context.
          </div>
        )}
      </div>

      <div
        className="border-b px-4 py-3.5"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        <div className="px-eyebrow mb-2.5">Copilot drafted these questions</div>
        <div className="flex flex-col gap-[7px]">
          {draftedQuestions.map((q, i) => (
            <div
              key={i}
              className="flex items-start gap-2 rounded-md border px-2.5 py-2 text-[12.5px]"
              style={{
                background: 'var(--px-surface)',
                borderColor: 'var(--px-hairline)',
                color: 'var(--px-fg-2)',
                lineHeight: 1.5,
              }}
            >
              <span
                className="px-mono mt-0.5 text-[10.5px]"
                style={{ color: 'var(--px-fg-4)' }}
              >
                Q{i + 1}
              </span>
              <span className="flex-1">{q}</span>
            </div>
          ))}
        </div>
      </div>

      {canManage && (
        <div className="px-4 py-3.5">
          <div className="px-eyebrow mb-2.5">Actions</div>
          <div className="flex flex-col gap-1">
            <InspectorAction
              label="Approve as must-have"
              keys={['⌘', '↵'] as const}
              primary
              onClick={() =>
                onUpdate({
                  priority: 'required',
                  weight: 3,
                  knockout: false,
                })
              }
            />
            <InspectorAction
              label="Mark deal-breaker"
              keys={['⇧', 'K'] as const}
              onClick={() =>
                onUpdate({
                  priority: 'required',
                  weight: 3,
                  knockout: true,
                })
              }
            />
            <InspectorAction
              label="Move to nice-to-have"
              keys={['⇧', 'D'] as const}
              onClick={() =>
                onUpdate({
                  priority: 'preferred',
                  knockout: false,
                })
              }
            />
            <InspectorAction
              label="Remove signal"
              keys={['⌫'] as const}
              danger
              onClick={() => {
                if (confirm(`Remove signal "${signal.value}"?`)) onRemove()
              }}
            />
          </div>
        </div>
      )}

      <div className="flex-1" />

      <div
        className="flex items-center gap-2 border-t px-4 py-2.5 text-[11px]"
        style={{
          background: 'var(--px-bg-2)',
          borderColor: 'var(--px-hairline)',
          color: 'var(--px-fg-4)',
        }}
      >
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: 'var(--px-accent)' }}
          aria-hidden="true"
        />
        Copilot · review changes before publishing
      </div>
    </aside>
  )
}

