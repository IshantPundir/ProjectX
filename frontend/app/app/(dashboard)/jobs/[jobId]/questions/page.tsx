'use client'

import { useEffect, useRef, useState } from 'react'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import Link from 'next/link'
import gsap from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'
import { useGSAP } from '@gsap/react'
import { useQueryClient } from '@tanstack/react-query'

import { Button } from '@/components/px'
import { useJob } from '@/lib/hooks/use-job'
import { useJobPipeline } from '@/lib/hooks/use-job-pipeline'
import { useBanksOverview } from '@/lib/hooks/use-banks-overview'
import { useBankWithQuestions } from '@/lib/hooks/use-bank-with-questions'
import { useConfirmBank } from '@/lib/hooks/use-confirm-bank'
import { useRegenerateQuestion } from '@/lib/hooks/use-regenerate-question'
import { useRefineQuestion } from '@/lib/hooks/use-refine-question'
import { useDraftQuestion } from '@/lib/hooks/use-draft-question'
import { useGenerateStageQuestions } from '@/lib/hooks/use-generate-questions'
import { useQuestionsStatusStream } from '@/lib/hooks/use-questions-status-stream'
import { questionsApi } from '@/lib/api/questions'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { stageSupportsQuestionBank } from '@/lib/pipelines/categories'
import { RefineQuestionDialog } from '@/components/dashboard/question-bank/RefineQuestionDialog'
import { AddQuestionDialog } from '@/components/dashboard/question-bank/AddQuestionDialog'
import type { BankResponse, QuestionResponse } from '@/lib/api/question-banks'
import type { PipelineStageResponse, StageType } from '@/lib/api/pipelines'

gsap.registerPlugin(useGSAP, ScrollTrigger)

/* ─── Small icons ────────────────────────────────────────── */

function SparkIcon({ size = 10 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.5 5.5l2.8 2.8M15.7 15.7l2.8 2.8M5.5 18.5l2.8-2.8M15.7 8.3l2.8-2.8" />
    </svg>
  )
}

function RefreshIcon({ size = 10 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21 12a9 9 0 11-3-6.7M21 4v4h-4" />
    </svg>
  )
}

function PlusIcon({ size = 10 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  )
}

/* ─── Stage-type display ─────────────────────────────────── */

const STAGE_TYPE_LABEL: Record<StageType, string> = {
  intake: 'intake',
  phone_screen: 'phone screen',
  ai_screening: 'AI screening',
  human_interview: 'human interview',
  debrief: 'debrief',
  take_home: 'take home',
}

type Mode = 'review' | 'interviewer'

/* ─── Page ────────────────────────────────────────────────── */

export default function QuestionBankPage() {
  const params = useParams<{ jobId: string }>()
  const router = useRouter()
  const searchParams = useSearchParams()
  const jobId = params.jobId

  const { data: job } = useJob(jobId)
  const { data: pipeline, isLoading: pipelineLoading } = useJobPipeline(jobId)
  const { data: overview, isLoading: overviewLoading } = useBanksOverview(jobId)

  const [mode, setMode] = useState<Mode>('review')

  // Stage selection from URL — matches pipeline page convention.
  const selectedStageId = searchParams.get('stage')

  // Live updates while banks generate. The hook invalidates ['banks', jobId]
  // and ['bank', jobId, selectedStageId] caches on every SSE event so the
  // pill row + active-stage detail re-render in real time.
  useQuestionsStatusStream(jobId, selectedStageId)

  // Auto-pick first bank-eligible stage if none selected (or if current
  // selection points to an intake/debrief stage that was filtered out).
  useEffect(() => {
    const selectableStages = pipeline?.stages.filter((s) =>
      stageSupportsQuestionBank(s.stage_type),
    ) ?? []

    const currentIsSelectable = selectableStages.some(
      (s) => s.id === selectedStageId,
    )
    if (selectedStageId && currentIsSelectable) return

    const first =
      overview?.banks
        .find((b) => b.status !== 'failed' && selectableStages.some((s) => s.id === b.stage_id))
        ?.stage_id ?? selectableStages[0]?.id
    if (first) {
      const qs = new URLSearchParams(searchParams.toString())
      qs.set('stage', first)
      router.replace(`/jobs/${jobId}/questions?${qs.toString()}`, { scroll: false })
    }
  }, [selectedStageId, overview, pipeline, router, searchParams, jobId])

  const selectStage = (stageId: string) => {
    const qs = new URLSearchParams(searchParams.toString())
    qs.set('stage', stageId)
    router.replace(`/jobs/${jobId}/questions?${qs.toString()}`, { scroll: false })
  }

  if (pipelineLoading || overviewLoading) {
    return (
      <div className="text-sm" style={{ color: 'var(--px-fg-3)' }}>
        Loading interview questions…
      </div>
    )
  }

  if (!job || !pipeline) {
    return (
      <div className="max-w-4xl">
        <p className="mt-4 text-sm" style={{ color: 'var(--px-fg-3)' }}>
          Question banks need a pipeline. Set one up first.
        </p>
        <Link href={`/jobs/${jobId}/pipeline`}>
          <Button size="sm" variant="outline" className="mt-3">
            Go to pipeline
          </Button>
        </Link>
      </div>
    )
  }

  const stages = pipeline.stages
  const banks = overview?.banks ?? []
  const currentBank = banks.find((b) => b.stage_id === selectedStageId) ?? null
  const currentStage =
    stages.find((s) => s.id === selectedStageId) ?? null

  return (
    <div className="-mx-8">
      {/* Per-stage switcher */}
      <div
        className="mb-4 flex items-center gap-2 overflow-x-auto border-b px-8 pb-3"
        style={{ borderColor: 'var(--px-hairline)' }}
      >
        <span
          className="mr-1.5 text-[11px] font-semibold uppercase"
          style={{ letterSpacing: '0.4px', color: 'var(--px-fg-4)' }}
        >
          Stages
        </span>
        {stages
          .filter((s) => stageSupportsQuestionBank(s.stage_type))
          .map((s, i) => (
            <StagePill
              key={s.id}
              index={i}
              stage={s}
              bank={banks.find((b) => b.stage_id === s.id) ?? null}
              active={selectedStageId === s.id}
              onClick={() => selectStage(s.id)}
            />
          ))}
        <div className="flex-1" />
        <div
          className="inline-flex rounded-md border p-0.5"
          style={{ background: 'var(--px-bg-2)', borderColor: 'var(--px-hairline)' }}
        >
          <ModeBtn active={mode === 'review'} onClick={() => setMode('review')}>
            Review
          </ModeBtn>
          <ModeBtn active={mode === 'interviewer'} onClick={() => setMode('interviewer')}>
            Interviewer view
          </ModeBtn>
        </div>
      </div>

      {!currentStage ? (
        <div className="px-8 text-sm" style={{ color: 'var(--px-fg-3)' }}>
          Pick a stage above.
        </div>
      ) : mode === 'review' ? (
        // `key` forces a full unmount/remount on stage change so
        // useGSAP's cleanup (gsap.context().revert()) tears down
        // pin-spacers before React reconciles. Without this, GSAP's
        // `pinSpacing: true` wrap of <main> leaves React expecting
        // <main> as a direct grid child while the browser has it
        // nested inside a .pin-spacer — the next stage switch then
        // throws NotFoundError on removeChild.
        <QBReview
          key={currentStage.id}
          jobId={jobId}
          stage={currentStage}
          bank={currentBank}
        />
      ) : (
        <QBInterviewer
          jobId={jobId}
          stage={currentStage}
        />
      )}
    </div>
  )
}

function StagePill({
  index,
  stage,
  bank,
  active,
  onClick,
}: {
  index: number
  stage: PipelineStageResponse
  bank: BankResponse | null
  active: boolean
  onClick: () => void
}) {
  const bgColor = active
    ? 'var(--px-surface)'
    : 'transparent'
  return (
    <button
      type="button"
      onClick={onClick}
      className="cursor-pointer rounded-full border text-left transition-colors"
      style={{
        padding: '6px 12px',
        background: bgColor,
        borderColor: active ? 'var(--px-fg-2)' : 'var(--px-hairline)',
        color: active ? 'var(--px-fg)' : 'var(--px-fg-3)',
      }}
    >
      <div className="flex items-center gap-1.5">
        <span
          className="px-mono text-[9.5px]"
          style={{ color: 'var(--px-fg-4)' }}
        >
          0{index + 1}
        </span>
        <span
          className="text-[12px]"
          style={{ fontWeight: active ? 500 : 400 }}
        >
          {stage.name}
        </span>
        {bank?.status === 'confirmed' && (
          <span
            className="px-mono text-[9.5px] font-semibold"
            style={{ color: 'var(--px-ok)' }}
          >
            ✓
          </span>
        )}
        {bank?.status === 'generating' && (
          <span
            className="text-[9.5px]"
            style={{ color: 'var(--px-accent)' }}
          >
            •••
          </span>
        )}
      </div>
    </button>
  )
}

function ModeBtn({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="cursor-pointer border-none"
      style={{
        height: 26,
        padding: '0 12px',
        background: active ? 'var(--px-surface)' : 'transparent',
        color: active ? 'var(--px-fg)' : 'var(--px-fg-3)',
        borderRadius: 5,
        fontSize: 12,
        fontWeight: active ? 500 : 400,
        boxShadow: active ? 'var(--px-shadow-sm)' : 'none',
      }}
    >
      {children}
    </button>
  )
}

/* ─── Review mode — master-detail ───────────────────────── */

function QBReview({
  jobId,
  stage,
  bank,
}: {
  jobId: string
  stage: PipelineStageResponse
  bank: BankResponse | null
}) {
  const { data: bankDetail, isLoading } = useBankWithQuestions(jobId, stage.id)
  const confirmMutation = useConfirmBank(jobId, stage.id)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [addOpen, setAddOpen] = useState(false)
  const draftMutation = useDraftQuestion(jobId, stage.id)
  const qc = useQueryClient()

  const containerRef = useRef<HTMLDivElement>(null)
  const pinWrapRef = useRef<HTMLDivElement>(null)
  const asideRef = useRef<HTMLElement>(null)
  const mainRef = useRef<HTMLElement>(null)

  const questions = bankDetail?.questions ?? []
  const mandatoryCount = questions.filter((q) => q.is_mandatory).length
  const totalMinutes = questions.reduce((s, q) => s + q.estimated_minutes, 0)
  // Derive selected directly — fall back to first question when the URL
  // hasn't picked one yet. Avoids a setState-in-effect cascade.
  const selected =
    questions.find((q) => q.id === selectedId) ?? questions[0] ?? null

  /*
    ScrollTrigger-driven pin.

    Why GSAP here instead of `position: sticky`: sticky's pin/unpin is a
    binary state flip with no way to ease the boundary. ScrollTrigger
    gives us:
      • `anticipatePin: 1` — avoids the 1-frame jitter at engagement
      • a scrubbed shadow tween on the lead-in — visually smooths the
        transition into and out of the pinned state
      • a deterministic scroll range keyed off the grid container

    The pin trigger is the grid wrapper. It engages when the grid's top
    reaches the bottom of the AppShell top bar and releases when the
    grid's bottom reaches the viewport bottom — matching the natural
    "stick while container is visible" behavior, but with explicit
    control over the edges. We read the top bar height from the same
    --px-topbar-h CSS token the rest of the app uses, so one token
    still drives everything.

    Dependencies force a revert + re-run when the stage or the number
    of questions changes, which is when the detail column's total
    height shifts and ScrollTrigger's cached measurements would go
    stale.
  */
  useGSAP(
    () => {
      if (
        !containerRef.current ||
        !pinWrapRef.current ||
        !asideRef.current ||
        !mainRef.current
      ) {
        return
      }

      const topbarH =
        parseFloat(
          getComputedStyle(document.documentElement)
            .getPropertyValue('--px-topbar-h')
            .trim(),
        ) || 48

      // Compute the horizontal gap between the grid's natural left
      // edge and the AppShell rail's right edge. When pinned, the
      // aside uses this as a CSS variable to shift leftward + widen +
      // add compensating padding so the aside visually reaches the
      // rail while the content inside stays stationary.
      //
      // Recomputed on rail collapse/expand (via the transitionend
      // listener below) so the pinned aside tracks the new gap
      // without needing a GSAP refresh.
      const syncPinShift = () => {
        if (!containerRef.current || !pinWrapRef.current) return
        const rail = document.querySelector<HTMLElement>(
          '[data-appshell-rail]',
        )
        const railRight = rail?.getBoundingClientRect().right ?? 0
        const gridLeft = containerRef.current.getBoundingClientRect().left
        const shift = Math.max(0, gridLeft - railRight)
        pinWrapRef.current.style.setProperty('--qb-pin-shift', `${shift}px`)
      }
      syncPinShift()

      // pinType defaults to 'fixed' — the browser-native path,
      // zero-jitter at any scroll speed. We deliberately do NOT use
      // 'transform' because its per-frame JS-driven translate lags
      // browser scroll by a frame, which users perceive as the pin
      // unpinning and re-pinning during fast scroll.
      //
      // The rail-collapse issue that 'transform' was trying to solve
      // (captured `left` goes stale when AppShell's nav rail width
      // transitions) is instead fixed by the `transitionend` listener
      // at the bottom of this callback, combined with
      // `invalidateOnRefresh: true` so refresh() re-captures `left`
      // against the new layout.
      //
      // ORDER still matters: main's pin uses pinSpacing:true which
      // grows the document by 240px. That layout change must land
      // BEFORE the aside's pin captures its `bottom bottom` end, or
      // the aside releases 240px too early.
      //
      // 240px symmetric hold window: at pin engagement we freeze main
      // for +=240 of scroll (pinSpacing consumes real scroll distance
      // — the user has to keep scrolling to advance through it). After
      // the hold, main unpins and scrolls normally while the aside
      // stays pinned. Symmetric by construction.
      ScrollTrigger.create({
        trigger: containerRef.current,
        start: `top ${topbarH}`,
        end: '+=240',
        pin: mainRef.current,
        pinSpacing: true,
        anticipatePin: 1,
        invalidateOnRefresh: true,
        id: 'qb-main-hold',
      })

      ScrollTrigger.create({
        trigger: containerRef.current,
        start: `top ${topbarH}`,
        // End past the document's max scroll so the pin never
        // releases within reachable scroll. Why: `bottom bottom`
        // ends the pin when the grid's bottom hits the viewport
        // bottom — but at that scroll, grid_top is far above the
        // viewport (grid is taller than the viewport), so the
        // aside's natural-flow position ends well above the viewport
        // bottom, producing a visible "shrunken panel" effect. By
        // extending end unreachable, the aside remains pinned at
        // top:48 with its full 100dvh-topbarH height all the way to
        // the end of the document. `+1000` is a safety buffer
        // against layout jitter between refresh and actual scroll.
        end: () => `+=${ScrollTrigger.maxScroll(window) + 1000}`,
        pin: pinWrapRef.current,
        pinSpacing: false,
        anticipatePin: 1,
        invalidateOnRefresh: true,
        // Pin the wrapper rather than the aside itself. GSAP sets
        // inline width/height on the pinned element; keeping the
        // wrapper as GSAP's target leaves the aside inside free to
        // grow leftward via CSS (margin + width + padding-left) when
        // .is-pinned is present on the wrapper. Zero !important,
        // zero style collisions with GSAP.
        toggleClass: {
          targets: pinWrapRef.current,
          className: 'is-pinned',
        },
        id: 'qb-aside-pin',
      })

      // Scrub a soft right-edge shadow in over the 120px leading up to
      // pin engagement, and back out on scroll-up. Pure transform /
      // shadow work — composited, no layout.
      gsap.fromTo(
        asideRef.current,
        { boxShadow: '0 0 0 0 rgba(58, 45, 28, 0)' },
        {
          boxShadow: '8px 0 22px -14px rgba(58, 45, 28, 0.18)',
          ease: 'none',
          scrollTrigger: {
            trigger: containerRef.current,
            start: `top ${topbarH + 120}`,
            end: `top ${topbarH}`,
            scrub: true,
            id: 'qb-aside-shadow',
          },
        },
      )

      // Safety net: recompute all trigger positions once the initial
      // layout (including pin-spacer injection) has settled. Cheap and
      // idempotent.
      ScrollTrigger.refresh()

      // Catch CSS width transitions that invalidate pinned `left`
      // coordinates. The specific case this solves: AppShell's nav
      // rail has a 180ms width transition on collapse/expand. Its
      // width change shifts JobLayout's centered position (max-w:1400
      // centered in a wider parent), which moves the grid container
      // horizontally. With pinType:'fixed' the aside's captured `left`
      // goes stale; `invalidateOnRefresh: true` + a refresh here makes
      // it re-measure against the post-transition layout.
      //
      // Filtered to `width` only so we don't thrash refresh() on
      // unrelated transitions (hover tints, color fades, etc.) — those
      // would add unnecessary work every time a user hovers any
      // button. Capture phase so we see every transition regardless
      // of which descendant fires it.
      const onTransitionEnd = (e: TransitionEvent) => {
        if (e.propertyName === 'width') {
          syncPinShift()
          ScrollTrigger.refresh()
        }
      }
      document.addEventListener('transitionend', onTransitionEnd, true)

      return () => {
        document.removeEventListener('transitionend', onTransitionEnd, true)
      }
    },
    {
      scope: containerRef,
      dependencies: [stage.id, questions.length],
      revertOnUpdate: true,
    },
  )

  if (isLoading) {
    return (
      <div className="px-8 text-sm" style={{ color: 'var(--px-fg-3)' }}>
        Loading bank…
      </div>
    )
  }

  if (!bankDetail) {
    return (
      <div className="px-8">
        <EmptyBankState jobId={jobId} stage={stage} />
      </div>
    )
  }

  if (questions.length === 0) {
    return (
      <div className="px-8">
        <EmptyBankState jobId={jobId} stage={stage} bank={bank} />
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className="grid items-start -mb-10"
      style={{ gridTemplateColumns: '380px 1fr' }}
    >
      {/*
        Pin wrapper — this is what GSAP ScrollTrigger pins. We keep it
        at the grid column's native 380px so the grid layout stays
        untouched, and `overflow: visible` (default) is what lets the
        aside inside spill leftward when .is-pinned is active.

        The aside is a block child filling the wrapper at rest. When
        .is-pinned is added (by ScrollTrigger.toggleClass), CSS
        transitions its margin-left, width, and padding-left together
        so the box slides left + grows leftward + compensates the
        content's x-position in one motion.
      */}
      <div
        ref={pinWrapRef}
        className="qb-pin-wrap h-[calc(100dvh-var(--px-topbar-h,48px))]"
      >
        <aside
          ref={asideRef}
          className="qb-aside flex h-full flex-col overflow-hidden border-r"
          style={{
            background: 'var(--px-bg-2)',
            borderColor: 'var(--px-hairline)',
          }}
        >
        <div
          className="border-b p-[18px]"
          style={{ borderColor: 'var(--px-hairline)' }}
        >
          <div
            className="mb-1 text-[10px] font-semibold uppercase"
            style={{ letterSpacing: '0.7px', color: 'var(--px-fg-4)' }}
          >
            Stage · {STAGE_TYPE_LABEL[stage.stage_type]}
          </div>
          <h2
            className="px-serif m-0 text-[22px] font-normal"
            style={{ letterSpacing: '-0.3px', color: 'var(--px-fg)' }}
          >
            {stage.name}
          </h2>

          <div
            className="mt-3.5 grid grid-cols-3 gap-2.5"
          >
            <Meter label="Questions" value={questions.length} />
            <Meter label="Mandatory" value={mandatoryCount} accent />
            <Meter
              label="Minutes"
              value={`${totalMinutes}/${stage.duration_minutes}`}
              bar={totalMinutes / stage.duration_minutes}
            />
          </div>

          {bankDetail.generated_at && (
            <div
              className="mt-3 flex items-center gap-1.5 text-[10.5px]"
              style={{ color: 'var(--px-fg-4)' }}
            >
              <SparkIcon size={10} />
              Copilot generated ·{' '}
              {new Date(bankDetail.generated_at).toLocaleDateString(undefined, {
                month: 'short',
                day: 'numeric',
              })}
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          {questions.map((q) => (
            <QBListRow
              key={q.id}
              q={q}
              selected={selected?.id === q.id}
              onClick={() => setSelectedId(q.id)}
            />
          ))}
        </div>

        <div
          className="flex items-center gap-1.5 border-t px-3.5 py-2.5"
          style={{ borderColor: 'var(--px-hairline)' }}
        >
          <button
            className="px-btn ghost xs"
            type="button"
            onClick={() => setAddOpen(true)}
          >
            <PlusIcon size={10} /> Add question
          </button>
          <div className="flex-1" />
          {bank?.status !== 'confirmed' && (
            <button
              className="px-btn primary xs"
              type="button"
              disabled={confirmMutation.isPending}
              onClick={() => confirmMutation.mutate()}
            >
              {confirmMutation.isPending ? 'Confirming…' : 'Confirm bank'}
            </button>
          )}
        </div>
        </aside>

      <AddQuestionDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        onDraft={async (body) => draftMutation.mutateAsync(body)}
        onAccept={async (body) => {
          const token = await getFreshSupabaseToken()
          await questionsApi.acceptDraft(token, jobId, stage.id, body)
          qc.invalidateQueries({ queryKey: ['bank', jobId, stage.id] })
        }}
      />
      </div>

      {/* Detail pane */}
      <main ref={mainRef} className="overflow-y-auto">
        {selected ? (
          <QBDetail jobId={jobId} stage={stage} q={selected} />
        ) : (
          <div
            className="p-8 text-sm"
            style={{ color: 'var(--px-fg-3)' }}
          >
            Pick a question on the left.
          </div>
        )}
      </main>

      {/*
        Left-extension transition. When .is-pinned lands on the pin
        wrapper, the aside's box grows leftward to meet the nav rail:
          • margin-left: 0    → -shift      (box slides left)
          • width:       100% → 100%+shift  (box expands leftward
                                            since the right edge stays
                                            pegged by the grid column)
        Content inside flows naturally into the widened panel — the
        header, list, and footer each pick up the extra horizontal
        space on their left side, matching the Claude.ai reference.
        --qb-pin-shift is set per-layout by syncPinShift() in useGSAP,
        recomputed whenever the rail's width transition ends.
      */}
      <style>{`
        .qb-aside {
          transition:
            margin-left 260ms cubic-bezier(0.2, 0.8, 0.3, 1),
            width       260ms cubic-bezier(0.2, 0.8, 0.3, 1);
        }
        .qb-pin-wrap.is-pinned .qb-aside {
          margin-left: calc(-1 * var(--qb-pin-shift, 0px));
          width:       calc(100% + var(--qb-pin-shift, 0px));
        }
      `}</style>
    </div>
  )
}

function Meter({
  label,
  value,
  accent,
  bar,
}: {
  label: string
  value: string | number
  accent?: boolean
  bar?: number
}) {
  return (
    <div>
      <div
        className="mb-0.5 text-[9px] font-semibold uppercase"
        style={{ letterSpacing: '0.6px', color: 'var(--px-fg-4)' }}
      >
        {label}
      </div>
      <div
        className="px-mono text-[15px] font-medium leading-none"
        style={{
          color: accent ? 'var(--px-accent)' : 'var(--px-fg)',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {value}
      </div>
      {bar !== undefined && (
        <div
          className="mt-1 h-[3px] overflow-hidden rounded-full"
          style={{ background: 'var(--px-surface-3)' }}
        >
          <div
            className="h-full"
            style={{
              width: `${Math.min(100, bar * 100)}%`,
              background: bar > 0.95 ? 'var(--px-caution)' : 'var(--px-accent)',
            }}
          />
        </div>
      )}
    </div>
  )
}

function QBListRow({
  q,
  selected,
  onClick,
}: {
  q: QuestionResponse
  selected: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="block w-full cursor-pointer border-none text-left"
      style={{
        padding: '12px 18px',
        background: selected ? 'var(--px-surface)' : 'transparent',
        borderLeft: selected ? '2px solid var(--px-accent)' : '2px solid transparent',
      }}
    >
      <div className="flex items-start gap-2.5">
        <span
          className="px-mono mt-px min-w-[16px] text-[11px] font-semibold"
          style={{
            color: selected ? 'var(--px-accent)' : 'var(--px-fg-4)',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {String(q.position + 1).padStart(2, '0')}
        </span>
        <div className="min-w-0 flex-1">
          <div
            className="line-clamp-2 text-[12.5px]"
            style={{
              color: 'var(--px-fg)',
              lineHeight: 1.45,
              fontWeight: selected ? 500 : 400,
            }}
          >
            {q.text}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {q.is_mandatory && (
              <span
                className="rounded border px-1.5 py-px text-[9px] font-bold"
                style={{
                  letterSpacing: '0.4px',
                  color: 'var(--px-accent)',
                  background: 'var(--px-accent-tint)',
                  borderColor: 'var(--px-accent-line)',
                }}
              >
                MUST
              </span>
            )}
            <span
              className="px-mono text-[10px]"
              style={{
                color: 'var(--px-fg-4)',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {q.estimated_minutes}m · {q.follow_ups.length} probe
              {q.follow_ups.length === 1 ? '' : 's'}
            </span>
          </div>
        </div>
      </div>
    </button>
  )
}

/* ─── Detail pane ────────────────────────────────────────── */

function QBDetail({
  jobId,
  stage,
  q,
}: {
  jobId: string
  stage: PipelineStageResponse
  q: QuestionResponse
}) {
  const regenMutation = useRegenerateQuestion(jobId, stage.id, q.id)
  const [isRegenerating, setIsRegenerating] = useState(false)
  const [refineOpen, setRefineOpen] = useState(false)
  const refineMutation = useRefineQuestion(jobId, stage.id, q.id)
  const qc = useQueryClient()

  const handleRegenerate = () => {
    setIsRegenerating(true)
    regenMutation.mutate(
      {},
      {
        onSettled: () => setIsRegenerating(false),
      },
    )
  }

  return (
    <div className="max-w-[900px] px-[34px] pb-12 pt-6">
      {/* Header */}
      <div className="mb-5 flex items-start gap-4">
        <div
          className="px-mono mt-0.5 text-[28px] font-medium leading-none"
          style={{
            color: 'var(--px-accent)',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {String(q.position + 1).padStart(2, '0')}
        </div>
        <div className="flex-1">
          <div className="mb-1.5 flex items-center gap-2">
            {q.is_mandatory && (
              <span
                className="rounded border px-1.5 py-0.5 text-[9.5px] font-bold"
                style={{
                  letterSpacing: '0.5px',
                  color: 'var(--px-accent)',
                  background: 'var(--px-accent-tint)',
                  borderColor: 'var(--px-accent-line)',
                }}
              >
                MANDATORY
              </span>
            )}
            <MetaBadge icon="⏱" label={`${q.estimated_minutes} min`} />
            <MetaBadge
              icon="⇢"
              label={`${q.follow_ups.length} probe${q.follow_ups.length === 1 ? '' : 's'}`}
            />
            <div className="flex-1" />
            <button
              type="button"
              className="px-btn ghost xs"
              onClick={() => setRefineOpen(true)}
            >
              <SparkIcon size={10} /> Refine
            </button>
            <button
              type="button"
              className="px-btn ghost xs"
              onClick={handleRegenerate}
              disabled={isRegenerating || regenMutation.isPending}
            >
              <span
                className="inline-block"
                style={{
                  animation:
                    isRegenerating || regenMutation.isPending
                      ? 'qbSpin 700ms linear infinite'
                      : 'none',
                }}
              >
                <RefreshIcon size={10} />
              </span>
              {isRegenerating || regenMutation.isPending
                ? 'Regenerating…'
                : 'Regenerate'}
            </button>
          </div>
          <h1
            className="px-serif m-0 text-[24px] font-normal"
            style={{ color: 'var(--px-fg)', letterSpacing: '-0.2px', lineHeight: 1.35 }}
          >
            {q.text}
          </h1>
          <div className="mt-3 flex flex-wrap items-center gap-1">
            <span
              className="mr-1 text-[10px] font-semibold uppercase"
              style={{ letterSpacing: '0.5px', color: 'var(--px-fg-4)' }}
            >
              Signals
            </span>
            {q.signal_values.map((s) => (
              <span
                key={s}
                className="rounded-full border px-2 py-0.5 text-[10.5px] font-medium"
                style={{
                  background: 'var(--px-accent-tint)',
                  color: 'var(--px-accent)',
                  borderColor: 'var(--px-accent-line)',
                }}
              >
                {s}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* Evaluation hint */}
      <div
        className="mb-5 rounded-md border px-4 py-3"
        style={{
          background: 'var(--px-bg-2)',
          borderColor: 'var(--px-hairline)',
          borderLeft: '3px solid var(--px-accent)',
        }}
      >
        <div
          className="mb-1 text-[10px] font-semibold uppercase"
          style={{ letterSpacing: '0.6px', color: 'var(--px-fg-4)' }}
        >
          Evaluation hint
        </div>
        <div
          className="text-[13px]"
          style={{ color: 'var(--px-fg-2)', lineHeight: 1.55 }}
        >
          {q.evaluation_hint}
        </div>
      </div>

      {/* Listen for / Red flags */}
      <div
        className="mb-5 grid gap-3.5"
        style={{ gridTemplateColumns: '1fr 1fr' }}
      >
        <CueList
          title="Listen for"
          items={q.positive_evidence}
          color="var(--px-ok)"
          bg="var(--px-ok-bg)"
          line="var(--px-ok-line)"
        />
        <CueList
          title="Red flags"
          items={q.red_flags}
          color="var(--px-danger)"
          bg="var(--px-danger-bg)"
          line="var(--px-danger-line)"
        />
      </div>

      {/* Follow-up probes */}
      {q.follow_ups.length > 0 && (
        <div className="mb-5">
          <div className="mb-2.5 flex items-baseline gap-2">
            <h3
              className="m-0 text-[13px] font-semibold"
              style={{ color: 'var(--px-fg)', letterSpacing: '-0.1px' }}
            >
              Follow-up probes
            </h3>
            <span className="text-[10.5px]" style={{ color: 'var(--px-fg-4)' }}>
              · use if the answer is thin or you want to go deeper
            </span>
          </div>
          <div
            className="flex flex-col gap-1.5 rounded-md border p-1.5"
            style={{
              background: 'var(--px-surface)',
              borderColor: 'var(--px-hairline)',
            }}
          >
            {q.follow_ups.map((p, i) => (
              <div
                key={i}
                className="flex items-start gap-3 px-3.5 py-2"
                style={{
                  borderBottom:
                    i < q.follow_ups.length - 1 ? '1px solid var(--px-hairline)' : 'none',
                }}
              >
                <span
                  className="px-mono mt-1 min-w-[20px] text-[10px]"
                  style={{
                    color: 'var(--px-fg-4)',
                    fontVariantNumeric: 'tabular-nums',
                  }}
                >
                  {String(i + 1).padStart(2, '0')}
                </span>
                <span
                  className="text-[13px] italic"
                  style={{ color: 'var(--px-fg-2)', lineHeight: 1.5 }}
                >
                  &ldquo;{p}&rdquo;
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Rubric — 3 tiers */}
      <div>
        <div className="mb-2.5 flex items-baseline gap-2">
          <h3
            className="m-0 text-[13px] font-semibold"
            style={{ color: 'var(--px-fg)', letterSpacing: '-0.1px' }}
          >
            Rubric
          </h3>
          <span className="text-[10.5px]" style={{ color: 'var(--px-fg-4)' }}>
            · score after the question, before moving on
          </span>
        </div>
        <div className="flex flex-col gap-2">
          <RubricTier
            tier="Exceeds"
            body={q.rubric.excellent}
            color="var(--px-ok)"
            icon="▲▲"
          />
          <RubricTier
            tier="Meets"
            body={q.rubric.meets_bar}
            color="var(--px-accent)"
            icon="▲"
          />
          <RubricTier
            tier="Below"
            body={q.rubric.below_bar}
            color="var(--px-danger)"
            icon="▽"
          />
        </div>
      </div>

      <style>{`@keyframes qbSpin { to { transform: rotate(360deg); } }`}</style>

      <RefineQuestionDialog
        open={refineOpen}
        onOpenChange={setRefineOpen}
        question={{
          id: q.id,
          text: q.text,
          signal_probed: q.signal_values[0] ?? '',
          mandatory: q.is_mandatory,
        }}
        onRefine={async (body) => refineMutation.mutateAsync(body)}
        onAccept={async (body) => {
          const token = await getFreshSupabaseToken()
          await questionsApi.acceptRefine(token, jobId, stage.id, q.id, body)
          qc.invalidateQueries({ queryKey: ['bank', jobId, stage.id] })
        }}
      />
    </div>
  )
}

function MetaBadge({ icon, label }: { icon: string; label: string }) {
  return (
    <span
      className="px-mono inline-flex items-center gap-1 rounded border px-1.5 text-[10.5px]"
      style={{
        height: 20,
        background: 'var(--px-surface-2)',
        borderColor: 'var(--px-hairline)',
        color: 'var(--px-fg-3)',
        fontVariantNumeric: 'tabular-nums',
      }}
    >
      <span className="opacity-70">{icon}</span>
      <span>{label}</span>
    </span>
  )
}

function CueList({
  title,
  items,
  color,
  bg,
  line,
}: {
  title: string
  items: string[]
  color: string
  bg: string
  line: string
}) {
  return (
    <div
      className="rounded-md border p-3.5"
      style={{ background: bg, borderColor: line }}
    >
      <div
        className="mb-2.5 flex items-center gap-1.5"
        style={{ color }}
      >
        <span
          className="text-[11px] font-bold uppercase"
          style={{ letterSpacing: '0.5px' }}
        >
          {title}
        </span>
        <span className="flex-1" />
        <span
          className="px-mono text-[10px] opacity-70"
          style={{ fontVariantNumeric: 'tabular-nums' }}
        >
          {String(items.length).padStart(2, '0')}
        </span>
      </div>
      {items.length === 0 ? (
        <div
          className="text-[11.5px] italic"
          style={{ color: 'var(--px-fg-4)' }}
        >
          None listed.
        </div>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
          {items.map((it, i) => (
            <li
              key={i}
              className="flex gap-2 text-[12.5px]"
              style={{ color: 'var(--px-fg-2)', lineHeight: 1.5 }}
            >
              <span style={{ color, flexShrink: 0, marginTop: 1 }}>·</span>
              <span>{it}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function RubricTier({
  tier,
  body,
  color,
  icon,
}: {
  tier: string
  body: string
  color: string
  icon: string
}) {
  return (
    <div
      className="grid gap-4 rounded-md border p-3.5"
      style={{
        gridTemplateColumns: '110px 1fr',
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
        borderLeft: `3px solid ${color}`,
      }}
    >
      <div>
        <div
          className="px-mono text-[11px] font-semibold opacity-85"
          style={{ color, letterSpacing: '0.5px' }}
        >
          {icon}
        </div>
        <div
          className="px-serif mt-1 text-[18px] font-normal"
          style={{ color, letterSpacing: '-0.2px' }}
        >
          {tier}
        </div>
      </div>
      <div
        className="text-[12.5px]"
        style={{ color: 'var(--px-fg-2)', lineHeight: 1.6 }}
      >
        {body}
      </div>
    </div>
  )
}

/* ─── Interviewer view — one question at a time ──────────── */

function QBInterviewer({
  jobId,
  stage,
}: {
  jobId: string
  stage: PipelineStageResponse
}) {
  const { data: bankDetail } = useBankWithQuestions(jobId, stage.id)
  const [idx, setIdx] = useState(0)

  const questions = bankDetail?.questions ?? []
  const q = questions[idx]
  const total = questions.length

  if (!bankDetail || questions.length === 0) {
    return (
      <div className="px-8 py-8">
        <EmptyBankState jobId={jobId} stage={stage} />
      </div>
    )
  }

  return (
    <div
      className="flex flex-col items-center pb-14 pt-8"
      style={{ background: 'var(--px-bg-2)', minHeight: 'calc(100vh - 14rem)' }}
    >
      {/* Progress strip */}
      <div
        className="mb-5 flex items-center gap-2.5"
        style={{ width: '100%', maxWidth: 880 }}
      >
        <div className="flex flex-1 gap-[3px]">
          {questions.map((qq, i) => (
            <button
              key={qq.id}
              type="button"
              onClick={() => setIdx(i)}
              aria-label={`Question ${i + 1}`}
              className="h-1 flex-1 cursor-pointer rounded-sm border-none p-0"
              style={{
                background:
                  i === idx
                    ? 'var(--px-accent)'
                    : i < idx
                      ? 'var(--px-ok)'
                      : 'var(--px-surface-3)',
              }}
            />
          ))}
        </div>
        <span
          className="px-mono text-[11px]"
          style={{
            color: 'var(--px-fg-4)',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {String(idx + 1).padStart(2, '0')} / {String(total).padStart(2, '0')}
        </span>
      </div>

      {/* Question card */}
      <div
        className="rounded-[12px] border"
        style={{
          width: '100%',
          maxWidth: 880,
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
          padding: '34px 40px',
          boxShadow: 'var(--px-shadow-sm)',
        }}
      >
        <div className="mb-4 flex items-center gap-2.5">
          <span
            className="px-mono text-[11px]"
            style={{
              color: 'var(--px-fg-4)',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            Q{String(q.position + 1).padStart(2, '0')}
          </span>
          {q.is_mandatory && (
            <span
              className="rounded border px-1.5 py-0.5 text-[9.5px] font-bold"
              style={{
                letterSpacing: '0.5px',
                color: 'var(--px-accent)',
                background: 'var(--px-accent-tint)',
                borderColor: 'var(--px-accent-line)',
              }}
            >
              MANDATORY
            </span>
          )}
          <MetaBadge icon="⏱" label={`${q.estimated_minutes} min`} />
          <div className="flex-1" />
          <span className="text-[11px]" style={{ color: 'var(--px-fg-4)' }}>
            {q.signal_values.slice(0, 2).join(' · ')}
          </span>
        </div>

        <h2
          className="px-serif m-0 text-[28px] font-normal"
          style={{
            color: 'var(--px-fg)',
            lineHeight: 1.35,
            letterSpacing: '-0.3px',
          }}
        >
          {q.text}
        </h2>

        <div
          className="mt-6 grid gap-3"
          style={{ gridTemplateColumns: '1fr 1fr' }}
        >
          <div
            className="rounded-md border p-3.5"
            style={{
              background: 'var(--px-ok-bg)',
              borderColor: 'var(--px-ok-line)',
            }}
          >
            <div
              className="mb-2 text-[10px] font-bold uppercase"
              style={{ letterSpacing: '0.6px', color: 'var(--px-ok)' }}
            >
              Listen for
            </div>
            <ul className="m-0 flex list-none flex-col gap-1 p-0">
              {q.positive_evidence.slice(0, 3).map((l, i) => (
                <li
                  key={i}
                  className="text-[11.5px]"
                  style={{ color: 'var(--px-fg-2)', lineHeight: 1.5 }}
                >
                  · {l}
                </li>
              ))}
            </ul>
          </div>
          <div
            className="rounded-md border p-3.5"
            style={{
              background: 'var(--px-danger-bg)',
              borderColor: 'var(--px-danger-line),',
            }}
          >
            <div
              className="mb-2 text-[10px] font-bold uppercase"
              style={{ letterSpacing: '0.6px', color: 'var(--px-danger)' }}
            >
              Red flags
            </div>
            <ul className="m-0 flex list-none flex-col gap-1 p-0">
              {q.red_flags.slice(0, 3).map((r, i) => (
                <li
                  key={i}
                  className="text-[11.5px]"
                  style={{ color: 'var(--px-fg-2)', lineHeight: 1.5 }}
                >
                  · {r}
                </li>
              ))}
            </ul>
          </div>
        </div>

        {q.follow_ups.length > 0 && (
          <details className="mt-4">
            <summary
              className="cursor-pointer text-[11.5px]"
              style={{ color: 'var(--px-fg-3)', letterSpacing: '0.3px' }}
            >
              Follow-up probes ({q.follow_ups.length})
            </summary>
            <div
              className="mt-2 flex flex-col gap-1 rounded-md p-3.5"
              style={{ background: 'var(--px-bg-2)' }}
            >
              {q.follow_ups.map((p, i) => (
                <div
                  key={i}
                  className="text-[12px] italic"
                  style={{ color: 'var(--px-fg-2)' }}
                >
                  &ldquo;{p}&rdquo;
                </div>
              ))}
            </div>
          </details>
        )}
      </div>

      {/* Scoring footer */}
      <div
        className="mt-5 rounded-[10px] border p-5"
        style={{
          width: '100%',
          maxWidth: 880,
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <div
          className="mb-2.5 text-[11px] font-semibold uppercase"
          style={{ letterSpacing: '0.6px', color: 'var(--px-fg-4)' }}
        >
          Score this answer
        </div>
        <div className="grid grid-cols-3 gap-2">
          <ScoreBtn tier="Exceeds" color="var(--px-ok)" />
          <ScoreBtn tier="Meets" color="var(--px-accent)" />
          <ScoreBtn tier="Below" color="var(--px-danger)" />
        </div>
      </div>

      {/* Nav */}
      <div
        className="mt-3.5 flex items-center gap-2"
        style={{ width: '100%', maxWidth: 880 }}
      >
        <button
          type="button"
          className="px-btn ghost sm"
          onClick={() => setIdx(Math.max(0, idx - 1))}
          disabled={idx === 0}
        >
          ← Prev
        </button>
        <div className="flex-1" />
        <button
          type="button"
          className="px-btn primary sm"
          onClick={() => setIdx(Math.min(total - 1, idx + 1))}
          disabled={idx === total - 1}
        >
          Next question →
        </button>
      </div>
    </div>
  )
}

function ScoreBtn({ tier, color }: { tier: string; color: string }) {
  return (
    <button
      type="button"
      className="cursor-pointer rounded-md border p-2.5 text-[12.5px] font-semibold transition-colors"
      style={{
        letterSpacing: '0.2px',
        borderColor: 'var(--px-hairline)',
        background: 'var(--px-bg-2)',
        color,
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = color
        e.currentTarget.style.color = '#fff'
        e.currentTarget.style.borderColor = color
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'var(--px-bg-2)'
        e.currentTarget.style.color = color
        e.currentTarget.style.borderColor = 'var(--px-hairline)'
      }}
    >
      {tier}
    </button>
  )
}

/* ─── Empty state: no bank generated yet ─────────────────── */

function EmptyBankState({
  jobId,
  stage,
  bank,
}: {
  jobId: string
  stage: PipelineStageResponse
  bank?: BankResponse | null
}) {
  const generateMutation = useGenerateStageQuestions(jobId, stage.id)
  const isGenerating =
    bank?.status === 'generating' || generateMutation.isPending
  return (
    <div
      className="rounded-[10px] border p-10 text-center"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <h2
        className="px-serif m-0 mb-2 text-2xl font-normal"
        style={{ color: 'var(--px-fg)' }}
      >
        No questions for {stage.name} yet
      </h2>
      <p
        className="mx-auto mb-6 max-w-lg text-sm"
        style={{ color: 'var(--px-fg-3)' }}
      >
        {isGenerating
          ? "Copilot is drafting questions scoped to this stage's signals."
          : 'Generate a question bank for this stage. Copilot will draft questions scoped to this stage’s signals.'}
      </p>
      {isGenerating ? (
        <div className="text-sm" style={{ color: 'var(--px-accent)' }}>
          <SparkIcon size={12} /> Generating…
        </div>
      ) : (
        <Button size="sm" onClick={() => generateMutation.mutate()}>
          Generate questions
        </Button>
      )}
    </div>
  )
}
