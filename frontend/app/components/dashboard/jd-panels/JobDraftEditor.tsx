'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import { Tabs } from '@/components/px'
import { ApiError } from '@/lib/api/client'
import type { JobPostingWithSnapshot } from '@/lib/api/jobs'
import { useExtractSignals } from '@/lib/hooks/use-extract-signals'
import { useTriggerEnrich } from '@/lib/hooks/use-trigger-enrich'
import { useUpdateJobDraft } from '@/lib/hooks/use-update-job-draft'

type DraftView = 'raw' | 'enriched'

type Props = {
  job: JobPostingWithSnapshot
}

/**
 * The /jobs/{id} view when status === 'draft'.
 *
 * Visual structure mirrors {@link JDReviewShell} (220px / 1fr / 380px
 * grid) so the same page maintains a stable shell across the lifecycle:
 *   - Left aside: signals rail (empty state in draft — signals don't
 *     exist yet)
 *   - Middle: Raw / Enriched JD tabs. Raw is editable here (textarea).
 *     Action bar below the canvas hosts "Enrich JD" + "Extract signals".
 *   - Right aside: reading-tips placeholder, same role as InspectorTips
 *     in the review shell.
 *
 * ATS-imported jobs land in this view with raw JD (and possibly enriched
 * copy) pre-filled — indistinguishable from a manually created job from
 * the recruiter's perspective. See docs/superpowers/specs/2026-05-14-
 * unified-job-creation-flow-design.md.
 */
export function JobDraftEditor({ job }: Props) {
  const updateDraft = useUpdateJobDraft(job.id)
  const triggerEnrich = useTriggerEnrich(job.id, { toasts: false })
  const extractSignals = useExtractSignals(job.id)

  const [rawJD, setRawJD] = useState(job.description_raw)
  const [projectScope, setProjectScope] = useState(job.project_scope_raw ?? '')
  // Default to the enriched tab when there's something to show there —
  // either a completed enrichment or one currently running. Otherwise
  // start on Raw so the recruiter sees the editable surface.
  const [view, setView] = useState<DraftView>(
    job.description_enriched || job.enrichment_status === 'streaming'
      ? 'enriched'
      : 'raw',
  )
  const [actionError, setActionError] = useState<ActionError | null>(null)

  // Sync local edits with server-side changes (e.g. ATS resync overwrites
  // description_raw). Guard: only adopt if the user hasn't typed since
  // the last save, otherwise we'd clobber their keystrokes.
  const lastSavedRaw = useRef(job.description_raw)
  const lastSavedScope = useRef(job.project_scope_raw ?? '')
  useEffect(() => {
    if (lastSavedRaw.current === rawJD && job.description_raw !== rawJD) {
      setRawJD(job.description_raw)
      lastSavedRaw.current = job.description_raw
    }
  }, [job.description_raw, rawJD])
  useEffect(() => {
    const incoming = job.project_scope_raw ?? ''
    if (lastSavedScope.current === projectScope && incoming !== projectScope) {
      setProjectScope(incoming)
      lastSavedScope.current = incoming
    }
  }, [job.project_scope_raw, projectScope])

  async function saveField(
    field: 'description_raw' | 'project_scope_raw',
    value: string,
  ) {
    const current = field === 'description_raw' ? lastSavedRaw : lastSavedScope
    if (current.current === value) return
    try {
      const next = value === '' && field === 'project_scope_raw' ? null : value
      await updateDraft.mutateAsync({ [field]: next } as Record<string, string | null>)
      current.current = value
    } catch (err) {
      toast.error(`Failed to save ${field}: ${(err as Error).message}`)
    }
  }

  async function handleEnrich() {
    setActionError(null)
    await saveField('description_raw', rawJD)
    try {
      await triggerEnrich.mutateAsync()
      setView('enriched')
    } catch (err) {
      setActionError(toActionError(err))
    }
  }

  async function handleExtract() {
    setActionError(null)
    await saveField('description_raw', rawJD)
    try {
      await extractSignals.mutateAsync()
    } catch (err) {
      setActionError(toActionError(err))
    }
  }

  const enrichInFlight = job.enrichment_status === 'streaming' || triggerEnrich.isPending
  const extractInFlight = extractSignals.isPending
  const rawJDEmpty = !rawJD.trim()
  const profileReady = job.profile_ready
  const blocked = !profileReady || rawJDEmpty || enrichInFlight || extractInFlight

  // Profile-readiness gate — same gate the backend enforces on
  // PATCH/enrich/extract. The recruiter sees a read-only blocked view
  // with a direct link to fix the underlying org_unit; no editable
  // inputs render, so there's no surface for accidental writes.
  if (!profileReady) {
    return (
      <div className="grid gap-3" style={{ gridTemplateColumns: '220px 1fr 380px' }}>
        <SignalsEmptyRail />
        <BlockedView job={job} />
        <BlockedTipsAside />
      </div>
    )
  }

  return (
    // items-stretch (default) keeps the sticky asides anchored within
    // their grid cell — same pattern as JDReviewShell.
    <div className="grid gap-3" style={{ gridTemplateColumns: '220px 1fr 380px' }}>
      <SignalsEmptyRail />

      <div className="flex min-w-0 flex-col gap-3">
        <DraftHeader job={job} />

        <Tabs<DraftView>
          ariaLabel="JD view"
          value={view}
          onChange={setView}
          items={[
            { value: 'raw', label: 'Raw JD' },
            {
              value: 'enriched',
              label: 'Enriched JD',
              disabled:
                !job.description_enriched && job.enrichment_status !== 'streaming',
              disabledHint: 'Click Enrich JD below to generate the enriched copy',
            },
          ]}
        />

        {view === 'raw' ? (
          <RawJdEditor
            rawJD={rawJD}
            onChange={setRawJD}
            onBlur={() => saveField('description_raw', rawJD)}
            projectScope={projectScope}
            onProjectScopeChange={setProjectScope}
            onProjectScopeBlur={() => saveField('project_scope_raw', projectScope)}
            saving={updateDraft.isPending}
          />
        ) : (
          <EnrichedJdPreview
            enriched={job.description_enriched}
            enrichmentStatus={job.enrichment_status}
            enrichmentError={job.enrichment_error}
          />
        )}

        {actionError && (
          <ActionErrorBanner
            error={actionError}
            onDismiss={() => setActionError(null)}
          />
        )}

        <ActionBar
          blocked={blocked}
          rawJDEmpty={rawJDEmpty}
          enrichInFlight={enrichInFlight}
          extractInFlight={extractInFlight}
          onEnrich={handleEnrich}
          onExtract={handleExtract}
        />
      </div>

      <DraftTipsAside />
    </div>
  )
}

/* ─── Header ──────────────────────────────────────────────────────── */

function DraftHeader({ job }: { job: JobPostingWithSnapshot }) {
  return (
    <header>
      <div className="mb-1.5 flex items-center gap-2 text-[11px]" style={{ color: 'var(--px-fg-4)' }}>
        <Link href="/jobs" style={{ color: 'var(--px-fg-3)' }}>
          ← Jobs
        </Link>
        <span>·</span>
        <span className="px-mono uppercase" style={{ letterSpacing: '0.4px' }}>
          Draft
        </span>
        {job.source !== 'native' && (
          <>
            <span>·</span>
            <span
              className="inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase"
              style={{
                letterSpacing: '0.3px',
                color: 'var(--px-accent)',
                background: 'var(--px-accent-tint)',
                borderColor: 'var(--px-accent-line)',
              }}
              title={`Imported from ${job.source}`}
            >
              from ATS
            </span>
          </>
        )}
      </div>
      <h1
        className="px-serif m-0 text-[26px] font-normal"
        style={{ letterSpacing: '-0.4px', color: 'var(--px-fg)' }}
      >
        {job.title}
      </h1>
      <div className="mt-1 text-[12.5px]" style={{ color: 'var(--px-fg-3)' }}>
        {job.org_unit_name ?? 'Unlinked org unit'} · paste the JD, then enrich
        or extract signals to advance.
      </div>
    </header>
  )
}

/* ─── Blocked view (profile_ready === false) ──────────────────────── */

function BlockedView({ job }: { job: JobPostingWithSnapshot }) {
  const hasContent = (job.description_raw || '').trim().length > 0
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <DraftHeader job={job} />

      <section
        className="rounded-[10px] border p-6"
        style={{
          background: 'var(--px-caution-bg, #fef3c7)',
          borderColor: 'var(--px-caution-line, #fde68a)',
          color: 'var(--px-caution, #92400e)',
        }}
        role="status"
      >
        <div className="mb-2 text-[18px] font-semibold">
          Complete the company profile first
        </div>
        <div className="text-[13px]" style={{ lineHeight: 1.6 }}>
          This role can&apos;t be configured until{' '}
          <b>{job.org_unit_name ?? 'its parent company'}</b> (or its parent
          company) has a complete profile — about, industry, and hiring bar.
          Copilot reads those when enriching the JD and extracting signals;
          editing the JD without them would just produce a draft no one can
          publish.
        </div>
        {job.org_unit_id && (
          <div className="mt-4">
            <Link
              href={`/settings/org-units/${job.org_unit_id}`}
              className="px-btn primary sm"
            >
              Complete the profile →
            </Link>
          </div>
        )}
      </section>

      {hasContent && (
        <section
          className="rounded-[10px] border p-5"
          style={{
            background: 'var(--px-surface)',
            borderColor: 'var(--px-hairline)',
          }}
        >
          <div
            className="px-eyebrow mb-2"
            style={{ margin: 0, color: 'var(--px-fg-4)' }}
          >
            Pre-filled raw JD (read-only)
          </div>
          <div className="mb-3 text-[12px]" style={{ color: 'var(--px-fg-3)' }}>
            From the ATS sync. Editable once the profile is complete.
          </div>
          <pre
            className="rounded-md border p-3 font-mono text-[13px]"
            style={{
              background: 'var(--px-bg-2)',
              borderColor: 'var(--px-hairline)',
              color: 'var(--px-fg-2)',
              whiteSpace: 'pre-wrap',
              lineHeight: 1.55,
              maxHeight: 380,
              overflow: 'auto',
            }}
          >
            {job.description_raw}
          </pre>
        </section>
      )}
    </div>
  )
}

function BlockedTipsAside() {
  return (
    <aside
      className="sticky self-start flex flex-col overflow-y-auto rounded-[10px] border px-4 py-5"
      style={{
        top: 60,
        maxHeight: 'calc(100vh - 72px)',
        background: 'var(--px-bg-2)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div className="px-eyebrow mb-3">Why this is blocked</div>
      <div
        className="text-[13px]"
        style={{ color: 'var(--px-fg-2)', lineHeight: 1.6 }}
      >
        Copilot grounds every JD enrichment + signal extraction in the
        company profile (<b>about</b>, <b>industry</b>, <b>hiring bar</b>).
        Without those, the output drifts toward generic copy that doesn&apos;t
        match how your team actually hires.
      </div>
      <div className="my-4 h-px" style={{ background: 'var(--px-hairline)' }} />
      <div
        className="text-[12.5px]"
        style={{ color: 'var(--px-fg-3)', lineHeight: 1.6 }}
      >
        Complete it once per company / client account — every role under that
        unit unlocks at the same time.
      </div>
    </aside>
  )
}

/* ─── Raw JD editor (center, raw tab) ─────────────────────────────── */

function RawJdEditor({
  rawJD,
  onChange,
  onBlur,
  projectScope,
  onProjectScopeChange,
  onProjectScopeBlur,
  saving,
}: {
  rawJD: string
  onChange: (v: string) => void
  onBlur: () => void
  projectScope: string
  onProjectScopeChange: (v: string) => void
  onProjectScopeBlur: () => void
  saving: boolean
}) {
  return (
    <section
      className="flex min-w-0 flex-col overflow-hidden rounded-[10px] border"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div className="flex flex-shrink-0 items-start justify-between gap-3 px-6 pb-3 pt-5">
        <div>
          <h2
            className="m-0 text-[20px] font-semibold"
            style={{ color: 'var(--px-fg)', letterSpacing: '-0.3px' }}
          >
            Raw JD
          </h2>
          <div className="mt-1 text-[12.5px]" style={{ color: 'var(--px-fg-3)' }}>
            Paste it. Saved automatically when you click out of the textarea.
          </div>
        </div>
        <SaveIndicator saving={saving} />
      </div>

      <div className="px-6 pb-6">
        <textarea
          className="px-input mono"
          value={rawJD}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          rows={18}
          placeholder="Paste the job description here…"
          style={{ width: '100%', fontSize: 13, lineHeight: 1.55 }}
        />

        <div className="mt-5">
          <div
            className="px-eyebrow flex items-baseline gap-2"
            style={{ margin: 0, color: 'var(--px-fg-4)' }}
          >
            <span>Project scope</span>
            <span
              className="text-[10.5px]"
              style={{
                fontWeight: 400,
                textTransform: 'none',
                letterSpacing: 0,
                color: 'var(--px-fg-4)',
              }}
            >
              optional
            </span>
          </div>
          <div className="mb-2 mt-0.5 text-[12.5px]" style={{ color: 'var(--px-fg-3)' }}>
            What will this hire build in their first 90 days? Sharpens
            generated questions.
          </div>
          <textarea
            className="px-input"
            value={projectScope}
            onChange={(e) => onProjectScopeChange(e.target.value)}
            onBlur={onProjectScopeBlur}
            rows={4}
            style={{ width: '100%', fontSize: 13, lineHeight: 1.55 }}
          />
        </div>
      </div>
    </section>
  )
}

function SaveIndicator({ saving }: { saving: boolean }) {
  return (
    <div className="px-mono text-[10.5px]" style={{ color: 'var(--px-fg-4)', letterSpacing: '0.3px' }}>
      {saving ? 'saving…' : 'saved'}
    </div>
  )
}

/* ─── Enriched JD preview (center, enriched tab) ──────────────────── */

function EnrichedJdPreview({
  enriched,
  enrichmentStatus,
  enrichmentError,
}: {
  enriched: string | null
  enrichmentStatus: 'idle' | 'streaming' | 'completed' | 'failed'
  enrichmentError: string | null
}) {
  return (
    <section
      className="flex min-w-0 flex-col overflow-hidden rounded-[10px] border"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div className="flex flex-shrink-0 items-start justify-between gap-3 px-6 pb-3 pt-5">
        <div>
          <h2
            className="m-0 text-[20px] font-semibold"
            style={{ color: 'var(--px-fg)', letterSpacing: '-0.3px' }}
          >
            Enriched JD
          </h2>
          <div className="mt-1 text-[12.5px]" style={{ color: 'var(--px-fg-3)' }}>
            Copilot&apos;s rewrite of the raw JD.
          </div>
        </div>
        <EnrichmentStatusBadge status={enrichmentStatus} />
      </div>

      <div className="px-6 pb-6">
        {enrichmentStatus === 'streaming' ? (
          <EmptyEnrichedState>
            Enriching… this typically takes 5–15 seconds.
          </EmptyEnrichedState>
        ) : enrichmentStatus === 'failed' ? (
          <EnrichmentFailedBanner error={enrichmentError} />
        ) : enriched ? (
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
              {enriched}
            </pre>
          </article>
        ) : (
          <EmptyEnrichedState>
            No enriched copy yet. Click <b>Enrich JD</b> below to generate one.
          </EmptyEnrichedState>
        )}
      </div>
    </section>
  )
}

function EnrichmentStatusBadge({
  status,
}: {
  status: 'idle' | 'streaming' | 'completed' | 'failed'
}) {
  const map = {
    idle: { label: 'idle', color: 'var(--px-fg-4)', bg: 'var(--px-surface-2)' },
    streaming: { label: 'running', color: 'var(--px-accent)', bg: 'var(--px-accent-tint)' },
    completed: { label: 'ready', color: 'var(--px-ok)', bg: 'var(--px-ok-bg)' },
    failed: { label: 'failed', color: 'var(--px-danger)', bg: 'var(--px-danger-bg)' },
  } as const
  const s = map[status]
  return (
    <span
      className="px-mono inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase"
      style={{
        letterSpacing: '0.4px',
        color: s.color,
        background: s.bg,
        borderColor: 'transparent',
      }}
    >
      {s.label}
    </span>
  )
}

function EmptyEnrichedState({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="rounded-md border p-6 text-[13px]"
      style={{
        background: 'var(--px-bg-2)',
        borderColor: 'var(--px-hairline)',
        color: 'var(--px-fg-3)',
        textAlign: 'center',
        minHeight: 280,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div>{children}</div>
    </div>
  )
}

function EnrichmentFailedBanner({ error }: { error: string | null }) {
  return (
    <div
      className="rounded-md border p-4 text-[13px]"
      style={{
        background: 'var(--px-danger-bg)',
        borderColor: 'var(--px-danger-line)',
        color: 'var(--px-danger)',
      }}
    >
      <div className="mb-1 font-semibold">Enrichment failed</div>
      <div>{error || 'Unknown error. Try again or contact support.'}</div>
    </div>
  )
}

/* ─── Action bar (center, below canvas) ───────────────────────────── */

function ActionBar({
  blocked,
  rawJDEmpty,
  enrichInFlight,
  extractInFlight,
  onEnrich,
  onExtract,
}: {
  blocked: boolean
  rawJDEmpty: boolean
  enrichInFlight: boolean
  extractInFlight: boolean
  onEnrich: () => void
  onExtract: () => void
}) {
  const hint = rawJDEmpty
    ? 'Paste a job description to enable enrichment and extraction.'
    : 'Enrich to clean up the JD, or extract signals directly from what you pasted.'
  return (
    <div
      className="flex items-center gap-3 rounded-[10px] border p-4"
      style={{ background: 'var(--px-bg-2)', borderColor: 'var(--px-hairline)' }}
    >
      <div className="flex-1 text-[12.5px]" style={{ color: 'var(--px-fg-3)' }}>
        {hint}
      </div>
      <button
        type="button"
        className="px-btn outline sm"
        onClick={onEnrich}
        disabled={blocked}
      >
        {enrichInFlight ? 'Enriching…' : 'Enrich JD'}
      </button>
      <button
        type="button"
        className="px-btn primary sm"
        onClick={onExtract}
        disabled={blocked}
      >
        {extractInFlight ? 'Starting…' : 'Extract signals →'}
      </button>
    </div>
  )
}

/* ─── Left aside: signals empty state ─────────────────────────────── */

function SignalsEmptyRail() {
  return (
    <aside
      className="sticky self-start flex flex-col overflow-y-auto rounded-[10px] border"
      style={{
        top: 60,
        maxHeight: 'calc(100vh - 72px)',
        background: 'var(--px-bg-2)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div className="px-3.5 pb-4 pt-4">
        <div className="px-eyebrow mb-2.5">Sections</div>
        <div
          className="rounded-md border p-3 text-[12px]"
          style={{
            background: 'var(--px-surface)',
            borderColor: 'var(--px-hairline)',
            color: 'var(--px-fg-3)',
            lineHeight: 1.55,
          }}
        >
          Signals appear here after extraction. Click{' '}
          <b style={{ color: 'var(--px-fg-2)' }}>Extract signals →</b> to
          generate must-haves, nice-to-haves, and the role snapshot.
        </div>
      </div>
    </aside>
  )
}

/* ─── Right aside: draft tips ─────────────────────────────────────── */

function DraftTipsAside() {
  return (
    <aside
      className="sticky self-start flex flex-col overflow-y-auto rounded-[10px] border px-4 py-5"
      style={{
        top: 60,
        maxHeight: 'calc(100vh - 72px)',
        background: 'var(--px-bg-2)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div className="px-eyebrow mb-3">Working with this draft</div>
      <div
        className="text-[13px]"
        style={{ color: 'var(--px-fg-2)', lineHeight: 1.6 }}
      >
        Paste the JD on the <b>Raw JD</b> tab. <b>Enrich JD</b> rewrites it
        through Copilot — useful when the source copy is rough.{' '}
        <b>Extract signals →</b> reads either version and produces the
        structured signals you&apos;ll review next.
      </div>
      <div
        className="my-4 h-px"
        style={{ background: 'var(--px-hairline)' }}
      />
      <div
        className="text-[12.5px]"
        style={{ color: 'var(--px-fg-3)', lineHeight: 1.6 }}
      >
        Editing is locked once signals are extracted, so it&apos;s easier to
        tweak the raw JD here than to re-run extraction later.
      </div>
    </aside>
  )
}

/* ─── 422 error mapping + banner ──────────────────────────────────── */

type ActionError =
  | { kind: 'empty_raw_jd'; message: string }
  | { kind: 'company_profile_incomplete'; message: string }
  | { kind: 'generic'; message: string }

function toActionError(err: unknown): ActionError {
  if (err instanceof ApiError) {
    if (err.code === 'empty_raw_jd') {
      return { kind: 'empty_raw_jd', message: err.message }
    }
    if (err.code === 'company_profile_incomplete') {
      return { kind: 'company_profile_incomplete', message: err.message }
    }
    return { kind: 'generic', message: err.message }
  }
  return { kind: 'generic', message: (err as Error).message || 'Action failed' }
}

function ActionErrorBanner({
  error,
  onDismiss,
}: {
  error: ActionError
  onDismiss: () => void
}) {
  const content = (() => {
    switch (error.kind) {
      case 'empty_raw_jd':
        return {
          title: 'Add the JD first',
          body: 'Paste the job description above, then try again.',
        }
      case 'company_profile_incomplete':
        return {
          title: 'Complete the company profile first',
          body: 'Copilot needs the company profile to enrich or extract signals.',
        }
      default:
        return { title: 'Something went wrong', body: error.message }
    }
  })()

  return (
    <div
      className="flex items-start gap-3 rounded-[10px] border p-3.5"
      style={{
        background: 'var(--px-caution-bg, #fef3c7)',
        borderColor: 'var(--px-caution-line, #fde68a)',
        color: 'var(--px-caution, #92400e)',
      }}
      role="alert"
    >
      <div className="flex-1">
        <div className="mb-1 text-[13px] font-semibold">{content.title}</div>
        <div className="text-[12.5px]" style={{ lineHeight: 1.55 }}>
          {content.body}
        </div>
      </div>
      <button
        type="button"
        className="px-btn ghost xs"
        onClick={onDismiss}
        aria-label="Dismiss"
      >
        Dismiss
      </button>
    </div>
  )
}
