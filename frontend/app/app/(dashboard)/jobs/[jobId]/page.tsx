'use client'

import { useEffect, useMemo, useState } from 'react'
import { useParams, useRouter, useSearchParams } from 'next/navigation'

import { LoadingSkeleton } from '@/components/dashboard/jd-panels/LoadingSkeleton'
import { ErrorBanner } from '@/components/dashboard/jd-panels/ErrorBanner'
import { Confidence } from '@/components/dashboard/jd-panels/components/Confidence'
import { SourceBadge } from '@/components/dashboard/jd-panels/components/SourceBadge'
import { Kbd } from '@/components/dashboard/jd-panels/components/Kbd'
import { EmptyRow } from '@/components/dashboard/jd-panels/components/EmptyRow'
import { groupSignals, type SignalWithIndex } from '@/components/dashboard/jd-panels/helpers/groupSignals'
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

/* ─── Icons ───────────────────────────────────────────────── */

function I({
  d,
  size = 14,
  stroke = 1.6,
}: {
  d: string | readonly string[]
  size?: number
  stroke?: number
}) {
  const paths = Array.isArray(d) ? d : [d as string]
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ flexShrink: 0 }}
      aria-hidden="true"
    >
      {paths.map((p, i) => (
        <path key={i} d={p} />
      ))}
    </svg>
  )
}

const ICONS = {
  warn: 'M10.3 3.9L2.7 17a2 2 0 001.7 3h15.2a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0zM12 9v4M12 17h.01',
  plus: 'M12 5v14M5 12h14',
  more: ['M5 12h.01', 'M12 12h.01', 'M19 12h.01'] as const,
  check: 'M20 6L9 17l-5-5',
  sparkle:
    'M12 3v4M12 17v4M3 12h4M17 12h4M5.5 5.5l2.8 2.8M15.7 15.7l2.8 2.8M5.5 18.5l2.8-2.8M15.7 8.3l2.8-2.8',
  refresh: 'M21 12a9 9 0 11-3-6.7L21 8M21 3v5h-5',
  eye: 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 15a3 3 0 100-6 3 3 0 000 6z',
} as const

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

/* ─── Left rail ──────────────────────────────────────────── */

type SectionId = 'must' | 'nice' | 'snapshot' | 'jd'

function SectionsRail({
  must,
  nice,
  hasSnapshot,
  totalCount,
  needsReviewCount,
  activeSection,
  filename,
  onShowJd,
  onJump,
}: {
  must: SignalWithIndex[]
  nice: SignalWithIndex[]
  hasSnapshot: boolean
  totalCount: number
  needsReviewCount: number
  activeSection: SectionId | null
  filename: string
  onShowJd: () => void
  onJump: (section: SectionId) => void
}) {
  // Mirror the order of actual sections in the center canvas, and hide
  // groups that aren't present for this role.
  const sections: { id: SectionId; label: string; count: number }[] = []
  if (must.length > 0)
    sections.push({ id: 'must', label: 'Must-haves', count: must.length })
  if (nice.length > 0)
    sections.push({ id: 'nice', label: 'Nice-to-haves', count: nice.length })
  if (hasSnapshot)
    sections.push({ id: 'snapshot', label: 'Role snapshot', count: 1 })
  sections.push({ id: 'jd', label: 'Full JD', count: 1 })

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
      <div className="px-3.5 pb-2 pt-4">
        <div className="px-eyebrow mb-2.5">Sections</div>
        {sections.map((s) => {
          const active = activeSection === s.id
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => (s.id === 'jd' ? onShowJd() : onJump(s.id))}
              className="mb-0.5 flex h-7 w-full cursor-pointer items-center gap-2 rounded-md border-none px-2.5 text-left text-[13px] transition-colors"
              style={{
                background: active ? 'var(--px-surface)' : 'transparent',
                color: active ? 'var(--px-fg)' : 'var(--px-fg-2)',
                borderLeft: active
                  ? '2px solid var(--px-accent)'
                  : '2px solid transparent',
              }}
              onMouseEnter={(e) => {
                if (!active)
                  e.currentTarget.style.background = 'var(--px-surface-2)'
              }}
              onMouseLeave={(e) => {
                if (!active) e.currentTarget.style.background = 'transparent'
              }}
            >
              <span className="flex-1 truncate">{s.label}</span>
              <span
                className="px-mono text-[11px]"
                style={{
                  color: 'var(--px-fg-4)',
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {s.count}
              </span>
            </button>
          )
        })}
      </div>

      <div className="flex-1" />

      {/* Counts summary */}
      <div
        className="border-t px-3.5 py-2.5"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        <div className="px-eyebrow mb-1.5">Summary</div>
        <div className="text-[12px]" style={{ color: 'var(--px-fg-3)' }}>
          <span
            className="px-mono"
            style={{ color: 'var(--px-fg)', fontVariantNumeric: 'tabular-nums' }}
          >
            {totalCount}
          </span>{' '}
          signals ·{' '}
          <span
            className="px-mono"
            style={{
              color: needsReviewCount > 0 ? 'var(--px-caution)' : 'var(--px-fg-4)',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {needsReviewCount}
          </span>{' '}
          to check
        </div>
      </div>

      {/* Original JD file card */}
      <div
        className="border-t px-3.5 py-3"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        <div className="px-eyebrow mb-2">Original JD</div>
        <button
          type="button"
          onClick={onShowJd}
          className="flex w-full cursor-pointer items-center gap-2.5 rounded-md border p-2 text-left text-[12px]"
          style={{
            background: 'var(--px-surface)',
            borderColor: 'var(--px-hairline)',
            color: 'var(--px-fg-3)',
          }}
        >
          <div
            className="flex items-center justify-center rounded-sm border"
            style={{
              width: 22,
              height: 28,
              background: 'var(--px-bg)',
              borderColor: 'var(--px-hairline-strong)',
            }}
          >
            <span
              className="px-mono text-[8px]"
              style={{ color: 'var(--px-fg-3)' }}
            >
              TXT
            </span>
          </div>
          <div className="min-w-0 flex-1">
            <div
              className="truncate text-[12px]"
              style={{ color: 'var(--px-fg)' }}
            >
              {filename}
            </div>
            <div className="text-[10.5px]" style={{ color: 'var(--px-fg-4)' }}>
              Click to read full
            </div>
          </div>
          <I d={ICONS.eye} size={12} />
        </button>
      </div>
    </aside>
  )
}

/* ─── Center canvas — signals view ───────────────────────── */

function SignalsCanvas({
  must,
  nice,
  job,
  stateBanner,
  isConfirmed,
  canManage,
  isDirty,
  saving,
  confirming,
  needsReviewCount,
  totalCount,
  focusIdx,
  onFocus,
  onSave,
  onSaveAndConfirm,
  onReEnrich,
}: {
  must: SignalWithIndex[]
  nice: SignalWithIndex[]
  job: JobPostingWithSnapshot
  stateBanner: string | null
  isConfirmed: boolean
  canManage: boolean
  isDirty: boolean
  saving: boolean
  confirming: boolean
  needsReviewCount: number
  totalCount: number
  focusIdx: number | null
  onFocus: (idx: number | null) => void
  onSave: () => void
  onSaveAndConfirm: () => void
  onReEnrich: () => void
}) {
  return (
    <main
      className="flex min-w-0 flex-col overflow-hidden rounded-[10px] border"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <CanvasHeader
        job={job}
        needsReviewCount={needsReviewCount}
        isConfirmed={isConfirmed}
      />
      <TabStrip
        totalCount={totalCount}
        isConfirmed={isConfirmed}
        canManage={canManage}
        isDirty={isDirty}
        saving={saving}
        confirming={confirming}
        onSave={onSave}
        onSaveAndConfirm={onSaveAndConfirm}
        onReEnrich={onReEnrich}
      />

      <div className="px-6 pb-6 pt-4">
        {stateBanner === 'low-confidence' && (
          <div
            className="mb-4 flex items-start gap-3 rounded-md border p-3.5"
            style={{
              background: 'var(--px-caution-bg)',
              borderColor: 'var(--px-caution-line)',
              color: 'var(--px-caution)',
            }}
          >
            <I d={ICONS.warn} size={16} />
            <div
              className="flex-1 text-[13px]"
              style={{ color: 'var(--px-fg-2)', lineHeight: 1.5 }}
            >
              <div
                className="mb-0.5 font-semibold"
                style={{ color: 'var(--px-caution)' }}
              >
                Copilot wasn&apos;t sure on a lot of this one.
              </div>
              The original JD was thin on specifics — I had to guess at several
              skills. Worth a slower read before confirming.
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  className="px-btn outline xs"
                  onClick={onReEnrich}
                >
                  Re-extract from JD
                </button>
              </div>
            </div>
          </div>
        )}

        <SignalGroup
          id="sig-group-must"
          title="Must-haves"
          count={must.length}
          helper="These block progress in the interview"
          emphasis
        >
          {must.map((s) => (
            <SignalRow
              key={s._i}
              s={s}
              rowId={`sig-row-${s._i}`}
              focused={focusIdx === s._i}
              onClick={() => onFocus(focusIdx === s._i ? null : s._i)}
            />
          ))}
          {must.length === 0 && <EmptyRow label="No must-haves yet." />}
        </SignalGroup>

        <SignalGroup
          id="sig-group-nice"
          title="Nice-to-haves"
          count={nice.length}
          helper="Optional follow-ups during the interview"
        >
          {nice.map((s) => (
            <SignalRow
              key={s._i}
              s={s}
              rowId={`sig-row-${s._i}`}
              focused={focusIdx === s._i}
              onClick={() => onFocus(focusIdx === s._i ? null : s._i)}
            />
          ))}
          {nice.length === 0 && <EmptyRow label="No nice-to-haves yet." />}
        </SignalGroup>

        <SignalGroup id="sig-group-snapshot" title="Role snapshot" count={1}>
          <div
            className="grid items-center gap-3 border-b px-3.5 py-2.5"
            style={{
              gridTemplateColumns: '84px 58px 1fr 120px 30px',
              borderColor: 'var(--px-hairline)',
            }}
          >
            <span className="px-chip soft" style={{ height: 20 }}>
              Role
            </span>
            <span />
            <div
              className="text-[13px]"
              style={{ color: 'var(--px-fg)' }}
            >
              <span className="font-medium">
                {job.latest_snapshot?.seniority_level
                  ? job.latest_snapshot.seniority_level
                      .charAt(0)
                      .toUpperCase() +
                    job.latest_snapshot.seniority_level.slice(1)
                  : '—'}
              </span>
              {job.latest_snapshot?.role_summary && (
                <span
                  className="ml-2 text-[12px]"
                  style={{ color: 'var(--px-fg-3)' }}
                >
                  · {job.latest_snapshot.role_summary}
                </span>
              )}
            </div>
            <div />
            <div />
          </div>
        </SignalGroup>

        <div className="h-6" />
      </div>
    </main>
  )
}

function CanvasHeader({
  job,
  needsReviewCount,
  isConfirmed,
}: {
  job: JobPostingWithSnapshot
  needsReviewCount: number
  isConfirmed: boolean
}) {
  const chips = isConfirmed ? (
    <>
      <span className="px-chip ok" style={{ height: 22 }}>
        <span className="px-dot" />
        live · accepting candidates
      </span>
    </>
  ) : needsReviewCount > 0 ? (
    <>
      <span className="px-chip ok" style={{ height: 22 }}>
        <span className="px-dot" />
        Ready to review
      </span>
      <span className="px-chip caution" style={{ height: 22 }}>
        <I d={ICONS.warn} size={10} />
        {needsReviewCount} to double-check
      </span>
    </>
  ) : (
    <span className="px-chip ok" style={{ height: 22 }}>
      <span className="px-dot" />
      Ready to publish
    </span>
  )

  const metaParts: string[] = []
  if (job.org_unit_name) metaParts.push(job.org_unit_name)
  if (job.location) metaParts.push(job.location)
  if (job.work_arrangement && job.work_arrangement !== 'onsite') {
    metaParts.push(job.work_arrangement === 'remote' ? 'Remote' : 'Hybrid 3d/wk')
  }
  if (job.salary_range_min && job.salary_range_max) {
    metaParts.push(
      `${job.salary_currency ?? ''} ${job.salary_range_min.toLocaleString()}–${job.salary_range_max.toLocaleString()}`,
    )
  }
  if (job.latest_snapshot?.seniority_level) {
    metaParts.push(
      job.latest_snapshot.seniority_level.charAt(0).toUpperCase() +
        job.latest_snapshot.seniority_level.slice(1),
    )
  }

  return (
    <div className="flex-shrink-0 px-6 pb-4 pt-5">
      <div className="mb-2 flex flex-wrap items-baseline gap-2.5">
        <h1
          className="m-0 text-[22px] font-semibold"
          style={{ color: 'var(--px-fg)', letterSpacing: '-0.4px' }}
        >
          What we found
        </h1>
        {chips}
      </div>
      {metaParts.length > 0 && (
        <div
          className="flex flex-wrap gap-2 text-[12.5px]"
          style={{ color: 'var(--px-fg-3)' }}
        >
          {metaParts.map((p, i) => (
            <span key={`${i}-${p}`} className="flex items-center gap-2">
              {i > 0 && <span style={{ color: 'var(--px-fg-4)' }}>·</span>}
              <span>{p}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function TabStrip({
  totalCount,
  isConfirmed,
  canManage,
  isDirty,
  saving,
  confirming,
  onSave,
  onSaveAndConfirm,
  onReEnrich,
}: {
  totalCount: number
  isConfirmed: boolean
  canManage: boolean
  isDirty: boolean
  saving: boolean
  confirming: boolean
  onSave: () => void
  onSaveAndConfirm: () => void
  onReEnrich: () => void
}) {
  return (
    <div
      className="flex h-10 flex-shrink-0 items-end gap-0 border-b px-6"
      style={{ background: 'var(--px-bg)', borderColor: 'var(--px-hairline)' }}
    >
      <div
        className="flex h-[39px] items-center gap-1.5 px-3.5 text-[13px] font-semibold"
        style={{
          color: 'var(--px-fg)',
          borderBottom: '2px solid var(--px-accent)',
        }}
      >
        Signals
        <span
          className="px-mono text-[10.5px]"
          style={{ color: 'var(--px-fg-4)', fontVariantNumeric: 'tabular-nums' }}
        >
          {totalCount}
        </span>
      </div>
      <div className="flex-1" />
      <div className="flex items-center gap-1.5 pb-1.5">
        <button
          type="button"
          className="px-btn ghost sm"
          onClick={onReEnrich}
          disabled={isConfirmed}
        >
          <I d={ICONS.refresh} size={11} />
          Ask Copilot again
        </button>
        {isConfirmed ? (
          <button type="button" className="px-btn outline sm" disabled>
            Locked · live
          </button>
        ) : canManage ? (
          <>
            {isDirty && (
              <button
                type="button"
                className="px-btn outline sm"
                onClick={onSave}
                disabled={saving}
              >
                {saving ? 'Saving…' : 'Save edits'}
              </button>
            )}
            <button
              type="button"
              className="px-btn primary sm"
              onClick={onSaveAndConfirm}
              disabled={saving || confirming}
            >
              <I d={ICONS.check} size={11} stroke={2.2} />
              {confirming
                ? 'Confirming…'
                : isDirty
                  ? 'Save & publish'
                  : 'Approve & publish'}
              <Kbd keys={['⌘', '↵']} />
            </button>
          </>
        ) : (
          <span className="text-[11.5px]" style={{ color: 'var(--px-fg-4)' }}>
            Read-only
          </span>
        )}
      </div>
    </div>
  )
}

function SignalGroup({
  id,
  title,
  count,
  helper,
  emphasis,
  children,
}: {
  id?: string
  title: string
  count: number
  helper?: string
  emphasis?: boolean
  children: React.ReactNode
}) {
  return (
    <section id={id} className="mb-[var(--px-group-gap)] scroll-mt-4">
      <div className="flex items-baseline gap-2.5 px-1 pb-2.5">
        <h2
          className="m-0 text-[14px] font-bold"
          style={{ color: 'var(--px-fg)', letterSpacing: '-0.1px' }}
        >
          {title}
        </h2>
        <span
          className="px-mono text-[11px]"
          style={{ color: 'var(--px-fg-4)', fontVariantNumeric: 'tabular-nums' }}
        >
          {count}
        </span>
        {helper && (
          <span
            className="text-[11.5px] italic"
            style={{ color: 'var(--px-fg-4)' }}
          >
            · {helper}
          </span>
        )}
      </div>
      <div
        className="overflow-hidden rounded-[10px] border"
        style={{
          background: 'var(--px-surface)',
          borderColor: emphasis ? 'var(--px-hairline-strong)' : 'var(--px-hairline)',
          boxShadow: emphasis ? 'var(--px-shadow-sm)' : 'none',
        }}
      >
        {children}
      </div>
    </section>
  )
}

function SignalRow({
  s,
  rowId,
  focused,
  onClick,
}: {
  s: SignalWithIndex
  rowId?: string
  focused: boolean
  onClick: () => void
}) {
  const confidence = weightToConfidence(s.weight)
  const flagReview = needsReview(s)
  return (
    <div
      id={rowId}
      onClick={onClick}
      className="grid cursor-pointer scroll-mt-4 items-center gap-3"
      style={{
        gridTemplateColumns: '84px 58px 1fr 120px 30px',
        minHeight: 'var(--px-row-h)',
        padding: 'var(--px-row-py) 14px',
        background: focused ? 'var(--px-accent-tint)' : 'transparent',
        borderBottom: '1px solid var(--px-hairline)',
        borderLeft: focused ? '2px solid var(--px-accent)' : '2px solid transparent',
        transition: 'background 120ms',
      }}
    >
      <SourceBadge kind={s.source} />
      {s.knockout ? (
        <span
          className="px-chip danger"
          style={{ height: 20, padding: '0 7px', fontSize: 10, fontWeight: 700, letterSpacing: 0.3 }}
        >
          MUST
        </span>
      ) : (
        <span />
      )}
      <div className="flex min-w-0 flex-wrap items-center gap-2.5">
        <span
          className="text-[14px] font-medium"
          style={{ color: 'var(--px-fg)' }}
        >
          {s.value}
        </span>
        {s.evaluation_hint && (
          <span
            className="truncate text-[11.5px] italic"
            style={{
              color: 'var(--px-fg-3)',
              paddingLeft: 10,
              marginLeft: 2,
              borderLeft: '2px solid var(--px-surface-3)',
              maxWidth: 280,
            }}
          >
            {s.evaluation_hint}
          </span>
        )}
        {flagReview && (
          <span
            className="px-chip caution"
            style={{ height: 18, padding: '0 6px', fontSize: 10 }}
          >
            <I d={ICONS.warn} size={9} />
            double-check
          </span>
        )}
      </div>
      <div className="flex justify-end">
        <Confidence value={confidence} />
      </div>
      <button
        type="button"
        className="px-btn ghost xs"
        aria-label="More"
        style={{ width: 26, padding: 0, justifyContent: 'center' }}
        onClick={(e) => e.stopPropagation()}
      >
        <I d={ICONS.more} size={12} />
      </button>
    </div>
  )
}

/* ─── Center canvas — Full JD view ──────────────────────── */

function FullJdCanvas({
  job,
  onReEnrich,
}: {
  job: JobPostingWithSnapshot
  onReEnrich: () => void
}) {
  const [which, setWhich] = useState<'enriched' | 'raw'>(
    job.description_enriched ? 'enriched' : 'raw',
  )
  const text = which === 'enriched' ? job.description_enriched : job.description_raw

  return (
    <main
      className="flex min-w-0 flex-col overflow-hidden rounded-[10px] border"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div className="flex-shrink-0 px-6 pb-4 pt-5">
        <h1
          className="m-0 text-[22px] font-semibold"
          style={{ color: 'var(--px-fg)', letterSpacing: '-0.4px' }}
        >
          Full JD
        </h1>
        <div className="mt-1 text-[12.5px]" style={{ color: 'var(--px-fg-3)' }}>
          {which === 'enriched'
            ? 'Rewritten by Copilot to match your company voice.'
            : 'Original text you pasted.'}
        </div>
      </div>

      <div
        className="flex h-10 flex-shrink-0 items-center gap-1.5 border-b px-6"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        {job.description_enriched && (
          <button
            type="button"
            className={`px-btn ${which === 'enriched' ? 'primary' : 'ghost'} xs`}
            onClick={() => setWhich('enriched')}
          >
            Enriched
          </button>
        )}
        <button
          type="button"
          className={`px-btn ${which === 'raw' ? 'primary' : 'ghost'} xs`}
          onClick={() => setWhich('raw')}
        >
          Raw
        </button>
        <div className="flex-1" />
        <button
          type="button"
          className="px-btn ghost xs"
          onClick={onReEnrich}
          disabled={job.is_confirmed}
        >
          <I d={ICONS.refresh} size={10} />
          Re-enrich
        </button>
      </div>

      <div className="px-6 pb-8 pt-5">
        <article
          className="rounded-[10px] border p-6"
          style={{
            background: 'var(--px-surface)',
            borderColor: 'var(--px-hairline)',
          }}
        >
          <pre
            className="px-serif m-0 whitespace-pre-wrap text-[14px]"
            style={{
              color: 'var(--px-fg-2)',
              lineHeight: 1.65,
              fontFamily: 'var(--font-serif)',
            }}
          >
            {text || 'No content.'}
          </pre>
        </article>

        {job.project_scope_raw && (
          <article
            className="mt-4 rounded-[10px] border p-6"
            style={{
              background: 'var(--px-surface)',
              borderColor: 'var(--px-hairline)',
            }}
          >
            <div className="px-eyebrow mb-2">Project scope</div>
            <pre
              className="m-0 whitespace-pre-wrap text-[13px]"
              style={{
                color: 'var(--px-fg-2)',
                lineHeight: 1.6,
                fontFamily: 'var(--font-sans)',
              }}
            >
              {job.project_scope_raw}
            </pre>
          </article>
        )}
      </div>
    </main>
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

function InspectorAction({
  label,
  keys,
  primary,
  danger,
  onClick,
}: {
  label: string
  keys: readonly string[]
  primary?: boolean
  danger?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-7 cursor-pointer items-center gap-2 rounded-md border-none px-2.5 text-[12.5px] transition-colors"
      style={{
        background: primary ? 'var(--px-accent-tint)' : 'transparent',
        color: danger
          ? 'var(--px-danger)'
          : primary
            ? 'var(--px-accent)'
            : 'var(--px-fg-2)',
        border: primary
          ? '1px solid var(--px-accent-line)'
          : '1px solid transparent',
        fontWeight: primary ? 500 : 400,
      }}
      onMouseEnter={(e) => {
        if (!primary) e.currentTarget.style.background = 'var(--px-surface-2)'
      }}
      onMouseLeave={(e) => {
        if (!primary) e.currentTarget.style.background = 'transparent'
      }}
    >
      <span className="flex-1 text-left">{label}</span>
      <Kbd keys={keys} />
    </button>
  )
}

function InspectorHint({
  needsReviewCount,
  isConfirmed,
}: {
  needsReviewCount: number
  isConfirmed: boolean
}) {
  return (
    <aside
      className="sticky self-start flex flex-col overflow-y-auto rounded-[10px] border px-4 py-5"
      style={{
        // 48px AppShell top bar + 12px gap = 60
        top: 60,
        maxHeight: 'calc(100vh - 72px)',
        background: 'var(--px-bg-2)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div className="px-eyebrow mb-3">Copilot</div>
      <div
        className="text-[13px]"
        style={{ color: 'var(--px-fg-2)', lineHeight: 1.6 }}
      >
        {isConfirmed ? (
          <>
            These signals are live. Any change here republishes to candidates
            in flight, so tread carefully.
          </>
        ) : needsReviewCount > 0 ? (
          <>
            I flagged <b>{needsReviewCount}</b> signal
            {needsReviewCount === 1 ? '' : 's'} as worth a second look. Click
            any row to see my reasoning and adjust.
          </>
        ) : (
          <>
            Signals look solid. Click any row to see where it came from in the
            JD and the questions I&apos;d ask around it.
          </>
        )}
      </div>

      <div
        className="my-4 h-px"
        style={{ background: 'var(--px-hairline)' }}
      />

      <div className="px-eyebrow mb-3">Tips</div>
      <ul
        className="m-0 flex flex-col gap-2 pl-4 text-[12.5px]"
        style={{ color: 'var(--px-fg-3)', lineHeight: 1.6 }}
      >
        <li>
          Click any signal to see &ldquo;where in the JD&rdquo; it came from.
        </li>
        <li>
          <span className="px-kbd">⌘</span>
          <span className="px-kbd">↵</span> approves &amp; publishes.
        </li>
        <li>Nothing auto-publishes — you approve the final version.</li>
      </ul>
    </aside>
  )
}

function InspectorTips() {
  return (
    <aside
      className="sticky self-start flex flex-col overflow-y-auto rounded-[10px] border px-4 py-5"
      style={{
        // 48px AppShell top bar + 12px gap = 60
        top: 60,
        maxHeight: 'calc(100vh - 72px)',
        background: 'var(--px-bg-2)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div className="px-eyebrow mb-3">Reading the JD</div>
      <div
        className="text-[13px]"
        style={{ color: 'var(--px-fg-2)', lineHeight: 1.6 }}
      >
        The enriched version is what candidates would see if you published
        today. Flip to <b>Raw</b> to compare against what you originally
        pasted.
      </div>
      <div
        className="my-4 h-px"
        style={{ background: 'var(--px-hairline)' }}
      />
      <div
        className="text-[12.5px]"
        style={{ color: 'var(--px-fg-3)', lineHeight: 1.6 }}
      >
        Switch back to <b>Signals</b> on the left when you&apos;re ready to
        review extracted signals.
      </div>
    </aside>
  )
}

/* ─── Snippet helpers ────────────────────────────────────── */

function SnippetHighlighted({ text, needle }: { text: string; needle: string }) {
  const i = text.toLowerCase().indexOf(needle.toLowerCase())
  if (i < 0) return <span>{text}</span>
  const before = text.slice(0, i)
  const match = text.slice(i, i + needle.length)
  const after = text.slice(i + needle.length)
  return (
    <>
      <span style={{ color: 'var(--px-fg-4)' }}>{before}</span>
      <span
        style={{
          background: 'var(--px-accent-tint)',
          color: 'var(--px-accent)',
          padding: '1px 4px',
          borderRadius: 3,
          fontWeight: 500,
        }}
      >
        {match}
      </span>
      <span style={{ color: 'var(--px-fg-4)' }}>{after}</span>
    </>
  )
}

