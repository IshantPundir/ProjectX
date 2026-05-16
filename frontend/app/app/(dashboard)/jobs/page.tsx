'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'

import {
  Button,
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/px'
import { JobStatusFilterDialog } from '@/components/settings/integrations/JobStatusFilterDialog'
import { SyncProgressBar } from '@/components/settings/integrations/SyncProgressBar'
import {
  getConnection,
  listConnections,
  listSyncLogs,
  type ATSConnection,
  type ATSSyncLog,
} from '@/lib/api/ats'
import { authApi, type MeResponse } from '@/lib/api/auth'
import {
  jobsApi,
  type JobPostingSummary,
  type JobStatus,
} from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { postedAgo } from '@/lib/utils'

/* ─── Status → design-system chip ─────────────────────────── */

type StatusKind =
  | 'draft'
  | 'reading'
  | 'review_signals'
  | 'in_review'
  | 'live'
  | 'failed'
  | 'archived'
  | 'blocked'

/**
 * Canonical jobs-list status pill. Vocabulary mirrors the layout chip in
 * `app/(dashboard)/jobs/[jobId]/layout.tsx::JobStatusChips` — the spec
 * (`docs/superpowers/specs/2026-05-15-job-activation-gate-design.md`)
 * defines one label per JobStatus, applied everywhere.
 *
 * `profile_ready=false` overrides the persisted status with a "blocked"
 * presentation: until the recruiter completes the parent company's
 * profile, the JD can't be configured (PATCH/enrich/extract all 422 on
 * the server). The DB row stays in 'draft' — the block is derived,
 * not a separate state.
 */
function statusKind(job: JobPostingSummary): StatusKind {
  if (!job.profile_ready) return 'blocked'
  const s: JobStatus = job.status
  if (s === 'draft') return 'draft'
  if (s === 'signals_extracting') return 'reading'
  if (s === 'signals_extracted') return 'review_signals'
  if (s === 'signals_extraction_failed') return 'failed'
  if (s === 'signals_confirmed' || s === 'pipeline_built') return 'in_review'
  if (s === 'active') return 'live'
  if (s === 'archived') return 'archived'
  // Defensive fallback for any future status — render the raw value.
  return 'draft'
}

function StatusPill({ job }: { job: JobPostingSummary }) {
  const kind = statusKind(job)
  const map: Record<StatusKind, { label: string; cls: string }> = {
    draft: { label: 'draft', cls: 'soft' },
    reading: { label: 'reading JD', cls: 'ai' },
    review_signals: { label: 'review signals', cls: 'caution' },
    in_review: { label: 'in review', cls: 'soft' },
    live: { label: 'live', cls: 'ok' },
    failed: { label: 'failed', cls: 'danger' },
    archived: { label: 'archived', cls: 'soft' },
    blocked: { label: 'awaiting setup', cls: 'caution' },
  }
  const m = map[kind]
  return (
    <span
      className={`px-chip ${m.cls} h-5 text-[10.5px] font-medium tracking-wide`}
      title={
        kind === 'blocked'
          ? 'Complete the parent company\'s profile to unlock JD configuration.'
          : undefined
      }
    >
      <span className="px-dot" />
      {m.label}
    </span>
  )
}

/** Compact provenance chip for ATS-imported jobs. */
function SourceChip({ source }: { source: string }) {
  if (!source.startsWith('ats_')) return null
  const vendor = source.replace('ats_', '')
  return (
    <span
      className="inline-flex items-center rounded-full border px-1.5 text-[9.5px] font-medium uppercase"
      style={{
        height: 16,
        letterSpacing: '0.4px',
        color: 'var(--px-fg-3)',
        background: 'var(--px-surface-2)',
        borderColor: 'var(--px-hairline)',
      }}
      title={`Imported from ${vendor}`}
    >
      From {vendor}
    </span>
  )
}

/**
 * Compact "Not set up" chip for ATS jobs that came in without a client
 * mapping (org_unit_id IS NULL). Under the unified job-creation flow,
 * an ATS job WITH a mapping just lands in `draft` — the recruiter opens
 * /jobs/{id} and proceeds as if they had created it manually. Profile
 * completion is checked when they click Enrich / Extract, not at sync
 * time.
 */
function NotSetUpChip({ job }: { job: JobPostingSummary }) {
  if (job.org_unit_id !== null) return null
  if (!job.source.startsWith('ats_')) return null
  return (
    <span
      className="inline-flex items-center rounded-full border px-1.5 text-[9.5px] font-medium uppercase"
      style={{
        height: 16,
        letterSpacing: '0.4px',
        color: 'var(--px-caution)',
        background: 'var(--px-caution-bg)',
        borderColor: 'var(--px-caution-line)',
      }}
      title="Imported from ATS but not linked to a company yet"
    >
      Not set up
    </span>
  )
}

/* ─── Grouping logic ──────────────────────────────────────── */

const IDLE_DAYS = 7
type Group = 'blocked' | 'needs_you' | 'in_review' | 'in_motion' | 'quiet'

function classifyJob(job: JobPostingSummary): Group {
  // Profile-readiness check trumps status: if the parent company's profile
  // is incomplete, the recruiter can't configure the JD at all. Surface
  // these as a distinct action item, separate from the "Needs you" queue.
  if (!job.profile_ready) return 'blocked'
  if (
    job.status === 'draft' ||
    job.status === 'signals_extracted' ||
    job.status === 'signals_extraction_failed'
  ) {
    return 'needs_you'
  }
  // signals_confirmed + pipeline_built collapse into "In review": the
  // pipeline exists but the recruiter still needs to add a middle stage,
  // confirm question banks, and click Activate before candidates can flow.
  // Distinct from "Needs you" (pre-pipeline action) and "In motion"
  // (actually accepting candidates) — matches the layout chip vocabulary.
  if (job.status === 'signals_confirmed' || job.status === 'pipeline_built') {
    return 'in_review'
  }
  const updated = new Date(job.updated_at).getTime()
  const ageDays = (Date.now() - updated) / (1000 * 60 * 60 * 24)
  if (ageDays > IDLE_DAYS) return 'quiet'
  return 'in_motion'
}

const GROUP_META: Record<Group, { heading: string; hint: string }> = {
  blocked: {
    heading: 'Blocked on setup',
    hint: 'parent company profile incomplete',
  },
  needs_you: { heading: 'Needs you', hint: 'awaiting your review or action' },
  in_review: {
    heading: 'In review',
    hint: 'pipeline set up — add a stage, confirm banks, then activate',
  },
  in_motion: { heading: 'In motion', hint: 'candidates actively progressing' },
  quiet: { heading: 'Quiet', hint: `no activity in ${IDLE_DAYS}+ days` },
}

type FilterId = 'all' | 'blocked' | 'needs_you' | 'in_review' | 'in_motion' | 'quiet'
type ViewId = 'table' | 'card' | 'kanban'

/* ─── Small SVG icons (sparkle, plus, filter) ─────────────── */

function SparkIcon({ size = 9 }: { size?: number }) {
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

function PlusIcon({ size = 12 }: { size?: number }) {
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

function FilterIcon({ size = 12 }: { size?: number }) {
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
      <path d="M22 3H2l8 9.5V19l4 2v-8.5z" />
    </svg>
  )
}

/* ─── View switcher ──────────────────────────────────────── */

function ViewSwitcher({ view, onChange }: { view: ViewId; onChange: (v: ViewId) => void }) {
  const items: { id: ViewId; icon: string }[] = [
    { id: 'table', icon: 'M3 6h18M3 12h18M3 18h18' },
    { id: 'card', icon: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z' },
    { id: 'kanban', icon: 'M3 3h7v18H3zM14 3h7v12h-7z' },
  ]
  return (
    <div
      className="inline-flex rounded-md border"
      style={{
        padding: 2,
        background: 'var(--px-surface-2)',
        borderColor: 'var(--px-hairline)',
        height: 28,
      }}
    >
      {items.map((v) => {
        const active = view === v.id
        return (
          <button
            key={v.id}
            type="button"
            onClick={() => onChange(v.id)}
            aria-label={`${v.id} view`}
            className="flex cursor-pointer items-center justify-center rounded-sm border-none"
            style={{
              width: 28,
              height: 22,
              background: active ? 'var(--px-surface)' : 'transparent',
              color: active ? 'var(--px-fg)' : 'var(--px-fg-4)',
              boxShadow: active ? '0 1px 2px rgba(0,0,0,0.06)' : 'none',
            }}
          >
            <svg
              width={12}
              height={12}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.8}
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d={v.icon} />
            </svg>
          </button>
        )
      })}
    </div>
  )
}

/* ─── Page ────────────────────────────────────────────────── */

export default function JobsListPage() {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [filter, setFilter] = useState<FilterId>('all')
  const [view, setView] = useState<ViewId>('table')
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [syncDialogOpen, setSyncDialogOpen] = useState(false)

  const { data, isLoading, error } = useQuery<JobPostingSummary[]>({
    queryKey: ['jobs-list'],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.list(token, undefined, { signal })
    },
  })

  // Whether the current user is super admin — drives Sync-from-ATS gating.
  const meQuery = useQuery<MeResponse>({
    queryKey: ['me'],
    queryFn: async () => authApi.me(await getFreshSupabaseToken()),
    staleTime: 60_000,
  })
  const isSuperAdmin = meQuery.data?.is_super_admin ?? false

  // ATS connections (drives "Sync jobs from ATS" visibility).
  const connectionsQuery = useQuery<ATSConnection[]>({
    queryKey: ['ats', 'connections'],
    queryFn: async () => listConnections(await getFreshSupabaseToken()),
    staleTime: 30_000,
  })
  const activeConnection = (connectionsQuery.data ?? []).find((c) => c.active)

  // The dialog needs the full ATSConnection (for `priorFilter`). The list
  // endpoint already returns it inline, so reading from connectionsQuery.data
  // avoids a second fetch when the recruiter opens the dialog.
  const connectionForDialog = useQuery<ATSConnection>({
    queryKey: ['ats', 'connection', activeConnection?.id],
    queryFn: async () =>
      getConnection(await getFreshSupabaseToken(), activeConnection!.id),
    enabled: !!activeConnection && syncDialogOpen,
  })

  // Poll sync-logs at 2s while a run is in progress, 10s otherwise.
  // We look at the MOST RECENT log only (the list comes back sorted
  // started_at desc). Older "running" rows can exist when an earlier
  // actor invocation crashed without finalizing — `.some(l => running)`
  // would lock the UI indefinitely on those orphans. The latest row
  // alone tells us whether *the current* sync is in flight.
  const syncLogsQuery = useQuery<ATSSyncLog[]>({
    queryKey: ['ats', 'connection', activeConnection?.id, 'sync-logs'],
    queryFn: async () =>
      listSyncLogs(await getFreshSupabaseToken(), activeConnection!.id),
    enabled: !!activeConnection,
    refetchInterval: (query) => {
      const latest = query.state.data?.[0]
      return latest?.status === 'running' ? 2000 : 10000
    },
  })
  const latestLog = syncLogsQuery.data?.[0]
  const isSyncRunning = latestLog?.status === 'running'
  // A sync log stuck in `running` for longer than this is almost
  // certainly stranded from a worker that died mid-sync (SIGKILL, OOM,
  // unhandled exception before the catch-all landed). Treat it as
  // re-triggerable so the user is never locked out by a dead worker;
  // the backend's trigger pre-check confirms-via-advisory-lock and
  // cleans the stale row up before enqueueing a fresh sync. A
  // legitimate large-tenant sync should finish well under this.
  const SYNC_STALE_AFTER_MS = 10 * 60 * 1000
  const isSyncStuck =
    isSyncRunning
    && latestLog
    && Date.now() - new Date(latestLog.started_at).getTime() > SYNC_STALE_AFTER_MS
  const canTriggerSync = !isSyncRunning || isSyncStuck
  // The new orchestrator (spec 2026-05-14) writes a flat counter map into
  // `entity_counts` only at completion, and leaves `progress` empty
  // during a run — there's no mid-sync denominator anymore. We keep this
  // accessor as a typed peek for any legacy log rows still in the DB; the
  // dialog falls back to the indeterminate "no denominator" branch when
  // progress.jobs is missing, which is the steady state under the new
  // model.
  const jobsProgress: { processed: number; total: number } | undefined =
    (latestLog?.progress as Record<string, unknown> | undefined)?.[
      'jobs'
    ] as { processed: number; total: number } | undefined
  const [progressDialogOpen, setProgressDialogOpen] = useState(false)
  // Set true the moment the trigger-sync mutation resolves; cleared
  // when the polling loop sees the new 'running' log (the worker has
  // actually started). Lets the dialog show a "Starting…" state during
  // the ~10s gap between trigger and the next idle-rate poll.
  const [syncJustTriggered, setSyncJustTriggered] = useState(false)
  const wasRunningRef = useRef(false)
  useEffect(() => {
    if (!wasRunningRef.current && isSyncRunning) {
      // Edge: a sync just started (from this page or another tab) — pop
      // the progress dialog so the recruiter can see counts roll in.
      setProgressDialogOpen(true)
      setSyncJustTriggered(false)
    } else if (wasRunningRef.current && !isSyncRunning) {
      // Edge: the run finished. We DON'T auto-close the dialog — when
      // there are no new jobs (cursor-based delta is empty) the whole
      // run finishes in ~3s, and silently closing the dialog gives the
      // recruiter no signal that anything happened. The dialog stays
      // open and rerenders into the "complete" branch (counts + a
      // close button). They dismiss when they're done reading.
      queryClient.invalidateQueries({ queryKey: ['jobs-list'] })
      toast.success('ATS jobs sync finished.')
    }
    wasRunningRef.current = !!isSyncRunning
  }, [isSyncRunning, queryClient])

  // While we're optimistically showing the popup, poll sync-logs at 1.5s
  // so the moment the worker commits Phase A (writes the 'running' row),
  // we see it on the next poll and transition the dialog out of "Starting…".
  // Safety timeout at 30s — if the worker never picks up the message
  // (Redis down, worker crashed) we clear the flag so the dialog stops
  // claiming "starting" indefinitely.
  useEffect(() => {
    if (!syncJustTriggered || !activeConnection) return
    const interval = setInterval(() => {
      queryClient.invalidateQueries({
        queryKey: ['ats', 'connection', activeConnection.id, 'sync-logs'],
      })
    }, 1500)
    const safetyTimeout = setTimeout(() => {
      setSyncJustTriggered(false)
    }, 30_000)
    return () => {
      clearInterval(interval)
      clearTimeout(safetyTimeout)
    }
  }, [syncJustTriggered, activeConnection, queryClient])

  const showSyncATSButton = isSuperAdmin && activeConnection !== undefined

  const deleteMutation = useMutation<void, Error, string[]>({
    mutationFn: async (ids) => {
      const token = await getFreshSupabaseToken()
      await Promise.all(ids.map((id) => jobsApi.delete(token, id)))
    },
    onSuccess: (_, ids) => {
      toast.success(`${ids.length} role${ids.length === 1 ? '' : 's'} deleted`)
      setSelected(new Set())
      queryClient.invalidateQueries({ queryKey: ['jobs-list'] })
    },
    onError: (err) => {
      toast.error(`Delete failed: ${err.message}`)
    },
  })

  const { groups, counts } = useMemo(() => {
    const buckets: Record<Group, JobPostingSummary[]> = {
      blocked: [],
      needs_you: [],
      in_review: [],
      in_motion: [],
      quiet: [],
    }
    for (const j of data ?? []) buckets[classifyJob(j)].push(j)
    const byDate = (a: JobPostingSummary, b: JobPostingSummary) =>
      new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
    for (const k of Object.keys(buckets) as Group[]) buckets[k].sort(byDate)
    return {
      groups: buckets,
      counts: {
        all: data?.length ?? 0,
        blocked: buckets.blocked.length,
        needs_you: buckets.needs_you.length,
        in_review: buckets.in_review.length,
        in_motion: buckets.in_motion.length,
        quiet: buckets.quiet.length,
      },
    }
  }, [data])

  // Blocked jobs surface first so the recruiter sees them before anything
  // else. Then "Needs you" (pre-pipeline action), then "In review"
  // (pipeline exists, waiting for activation), then steady-state.
  const visibleGroups = useMemo<Group[]>(() => {
    if (filter === 'all') return ['blocked', 'needs_you', 'in_review', 'in_motion', 'quiet']
    return [filter]
  }, [filter])

  const flatFiltered = useMemo(
    () => visibleGroups.flatMap((g) => groups[g]),
    [groups, visibleGroups],
  )

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAll() {
    if (flatFiltered.length === 0) return
    if (selected.size === flatFiltered.length) setSelected(new Set())
    else setSelected(new Set(flatFiltered.map((j) => j.id)))
  }

  function handleBulkDelete() {
    if (selected.size === 0) return
    setConfirmOpen(true)
  }

  function confirmBulkDelete() {
    setConfirmOpen(false)
    deleteMutation.mutate([...selected])
  }

  const allSelected = flatFiltered.length > 0 && selected.size === flatFiltered.length
  const isEmpty = !isLoading && !error && (data?.length ?? 0) === 0

  return (
    <div className="mx-auto max-w-[1400px] px-8 pb-10 pt-[22px]">
      {/* Header — serif title + inline metadata + bulk-action + primary CTA */}
      <div className="mb-5 flex items-end gap-4">
        <div>
          <h1
            className="px-serif m-0 text-[30px] font-normal"
            style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
          >
            Roles
          </h1>
          <div className="mt-1 text-[12.5px]" style={{ color: 'var(--px-fg-3)' }}>
            {isLoading ? (
              <span>Loading…</span>
            ) : (
              <>
                <span>{counts.in_motion} open</span>
                <span className="mx-1.5" style={{ color: 'var(--px-fg-4)' }}>·</span>
                <span>{counts.needs_you} needs you</span>
                <span className="mx-1.5" style={{ color: 'var(--px-fg-4)' }}>·</span>
                <span>{counts.in_review} in review</span>
                <span className="mx-1.5" style={{ color: 'var(--px-fg-4)' }}>·</span>
                <span>{counts.quiet} quiet</span>
              </>
            )}
          </div>
        </div>
        <div className="flex-1" />

        {selected.size > 0 && (
          <Button
            variant="destructive"
            size="sm"
            disabled={deleteMutation.isPending}
            onClick={handleBulkDelete}
          >
            {deleteMutation.isPending ? 'Deleting…' : `Delete ${selected.size}`}
          </Button>
        )}

        <button className="px-btn ghost sm" type="button">
          <FilterIcon size={12} />
          Filter
        </button>

        {showSyncATSButton && (
          <button
            type="button"
            onClick={() => setSyncDialogOpen(true)}
            disabled={!canTriggerSync}
            className="px-btn outline sm"
            title={
              isSyncStuck
                ? 'Previous sync appears stuck. Click to re-trigger; '
                  + 'the backend will reclaim it if no worker is alive.'
                : undefined
            }
          >
            {isSyncStuck
              ? 'Re-trigger sync'
              : isSyncRunning
                ? 'Syncing…'
                : 'Sync jobs from ATS'}
          </button>
        )}

        <Link
          href="/jobs/new"
          className="px-btn primary sm"
        >
          <PlusIcon size={12} />
          New role
        </Link>
      </div>

      <div className="mb-3.5 flex items-center gap-1.5">
        {(
          [
            { id: 'all', label: 'All', n: counts.all },
            ...(counts.blocked > 0
              ? [{ id: 'blocked' as const, label: 'Blocked on setup', n: counts.blocked }]
              : []),
            { id: 'needs_you', label: 'Needs you', n: counts.needs_you },
            { id: 'in_review', label: 'In review', n: counts.in_review },
            { id: 'in_motion', label: 'In motion', n: counts.in_motion },
            { id: 'quiet', label: 'Quiet', n: counts.quiet },
          ] as const
        ).map((p) => {
          const active = filter === p.id
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => setFilter(p.id)}
              className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border text-[12px] transition-colors"
              style={{
                height: 26,
                padding: '0 10px',
                borderColor: active ? 'var(--px-fg-2)' : 'transparent',
                background: active ? 'var(--px-surface)' : 'transparent',
                color: active ? 'var(--px-fg)' : 'var(--px-fg-3)',
                fontWeight: active ? 500 : 400,
              }}
            >
              {p.label}
              {p.n > 0 && (
                <span
                  className="px-mono text-[10.5px]"
                  style={{ color: 'var(--px-fg-4)', fontVariantNumeric: 'tabular-nums' }}
                >
                  {p.n}
                </span>
              )}
            </button>
          )
        })}
        <div className="flex-1" />
        <span className="text-[11.5px]" style={{ color: 'var(--px-fg-4)' }}>
          Sorted by activity
        </span>
        <ViewSwitcher view={view} onChange={setView} />
      </div>

      {/* Body */}
      {isLoading ? (
        <div className="text-sm" style={{ color: 'var(--px-fg-3)' }}>
          Loading…
        </div>
      ) : error ? (
        <div className="text-sm" style={{ color: 'var(--px-danger)' }}>
          Error: {(error as Error).message}
        </div>
      ) : isEmpty ? (
        <EmptyState />
      ) : view === 'table' ? (
        <GroupedTable
          visibleGroups={visibleGroups}
          groups={groups}
          selected={selected}
          allSelected={allSelected}
          onToggleAll={toggleAll}
          onToggle={toggleSelect}
        />
      ) : view === 'card' ? (
        <CardGrid rows={flatFiltered} />
      ) : (
        <KanbanStub />
      )}

      {activeConnection && (
        <JobStatusFilterDialog
          open={syncDialogOpen}
          onClose={() => setSyncDialogOpen(false)}
          connectionId={activeConnection.id}
          priorFilter={connectionForDialog.data?.job_status_filter ?? null}
          triggerSyncOnSave
          onSyncTriggered={() => {
            // Open the progress popup immediately, before the polling
            // loop has had a chance to see the new 'running' log row.
            // The dialog body shows a "Starting…" state until the next
            // poll catches up (typically within 2s of the worker
            // committing Phase A of the actor).
            setSyncJustTriggered(true)
            setProgressDialogOpen(true)
          }}
        />
      )}

      {/* Progress dialog — auto-opens when the latest sync_log flips to
          'running'. After the run finishes, the dialog stays open and
          re-renders into the "complete" branch so the recruiter can see
          counts (or "already up to date" when the cursor-based delta
          turned up zero new jobs). Closing the dialog never cancels the
          sync — the actor runs independently. */}
      <SyncJobsProgressDialog
        open={progressDialogOpen}
        onOpenChange={setProgressDialogOpen}
        log={latestLog}
        isRunning={isSyncRunning}
        isStarting={syncJustTriggered && !isSyncRunning}
      />

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogTitle>Delete {selected.size} role{selected.size === 1 ? '' : 's'}?</DialogTitle>
          <DialogDescription>
            This permanently removes the selected role{selected.size === 1 ? '' : 's'}. This cannot be undone.
          </DialogDescription>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setConfirmOpen(false)}
              disabled={deleteMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={confirmBulkDelete}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? 'Deleting…' : 'Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

/* ─── Table view ─────────────────────────────────────────── */

function GroupedTable({
  visibleGroups,
  groups,
  selected,
  allSelected,
  onToggleAll,
  onToggle,
}: {
  visibleGroups: Group[]
  groups: Record<Group, JobPostingSummary[]>
  selected: Set<string>
  allSelected: boolean
  onToggleAll: () => void
  onToggle: (id: string) => void
}) {
  return (
    <div
      className="overflow-hidden rounded-[10px] border"
      style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
    >
      {/* Column header */}
      <div
        className="grid items-center gap-4 text-[10px] font-medium uppercase"
        style={{
          gridTemplateColumns: '40px minmax(0,2.2fr) 120px 70px 80px 140px 110px',
          padding: '9px 18px',
          letterSpacing: '0.8px',
          color: 'var(--px-fg-4)',
          background: 'var(--px-bg-2)',
          borderBottom: '1px solid var(--px-hairline)',
        }}
      >
        <input
          type="checkbox"
          className="px-check"
          checked={allSelected}
          onChange={onToggleAll}
          aria-label="Select all"
        />
        <span>Role</span>
        <span>Status</span>
        <span className="text-right">Signals</span>
        <span className="text-right">Moving</span>
        <span>Created by</span>
        <span>Posted</span>
      </div>

      {/* Grouped rows */}
      {visibleGroups.map((g) => {
        const rows = groups[g]
        if (rows.length === 0) return null
        return (
          <div key={g}>
            <div
              className="flex items-baseline gap-2"
              style={{
                padding: '12px 18px 8px',
                background: 'var(--px-bg-2)',
                borderBottom: '1px solid var(--px-hairline)',
              }}
            >
              <span
                className="text-[11px] font-semibold"
                style={{ letterSpacing: '0.2px', color: 'var(--px-fg-2)' }}
              >
                {GROUP_META[g].heading}
              </span>
              <span className="text-[11px]" style={{ color: 'var(--px-fg-4)' }}>
                · {GROUP_META[g].hint}
              </span>
              <div className="flex-1" />
              <span
                className="px-mono text-[10.5px]"
                style={{ color: 'var(--px-fg-4)', fontVariantNumeric: 'tabular-nums' }}
              >
                {rows.length}
              </span>
            </div>
            {rows.map((job, i) => {
              const checked = selected.has(job.id)
              const hasAiHint = job.needs_review_count > 0
              return (
                <JobsRow
                  key={job.id}
                  job={job}
                  checked={checked}
                  hasAiHint={hasAiHint}
                  last={i === rows.length - 1}
                  onToggle={() => onToggle(job.id)}
                />
              )
            })}
          </div>
        )
      })}
    </div>
  )
}

function JobsRow({
  job,
  checked,
  hasAiHint,
  last,
  onToggle,
}: {
  job: JobPostingSummary
  checked: boolean
  hasAiHint: boolean
  last: boolean
  onToggle: () => void
}) {
  const [hov, setHov] = useState(false)
  return (
    <div
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      className="grid items-center gap-4 text-[12.5px]"
      style={{
        gridTemplateColumns: '40px minmax(0,2.2fr) 120px 70px 80px 140px 110px',
        padding: '12px 18px',
        borderBottom: last ? 'none' : '1px solid var(--px-hairline)',
        background: checked
          ? 'var(--px-accent-tint)'
          : hov
            ? 'var(--px-bg-2)'
            : 'transparent',
        transition: 'background 80ms',
      }}
    >
      <input
        type="checkbox"
        className="px-check"
        checked={checked}
        onChange={onToggle}
        aria-label={`Select ${job.title}`}
      />
      <div className="min-w-0">
        <div className="mb-0.5 flex items-center gap-2">
          <Link
            href={`/jobs/${job.id}`}
            className="truncate text-[13.5px] font-medium hover:underline"
            style={{ color: 'var(--px-fg)' }}
          >
            {job.title}
          </Link>
          <SourceChip source={job.source} />
          <NotSetUpChip job={job} />
        </div>
        <div
          className="flex items-center gap-1.5 text-[11.5px]"
          style={{ color: 'var(--px-fg-4)' }}
        >
          <span>{job.org_unit_name ?? '—'}</span>
          {hasAiHint && (
            <>
              <span>·</span>
              <span
                className="inline-flex items-center gap-1"
                style={{ color: 'var(--px-accent)' }}
              >
                <SparkIcon size={9} />
                {job.needs_review_count} signal
                {job.needs_review_count === 1 ? '' : 's'} to double-check
              </span>
            </>
          )}
        </div>
      </div>
      <div>
        <StatusPill job={job} />
      </div>
      <div
        className="px-mono text-right text-[12.5px]"
        style={{ color: 'var(--px-fg-2)', fontVariantNumeric: 'tabular-nums' }}
      >
        {job.signal_count > 0 ? job.signal_count : '—'}
      </div>
      <div
        className="px-mono text-right text-[12.5px]"
        style={{ color: 'var(--px-fg-4)', fontVariantNumeric: 'tabular-nums' }}
      >
        —
      </div>
      <div
        className="truncate text-[12px]"
        style={{ color: 'var(--px-fg-2)' }}
        title={job.created_by_email ?? undefined}
      >
        {job.created_by_email ?? '—'}
      </div>
      <div className="text-[12px]" style={{ color: 'var(--px-fg-4)' }}>
        {postedAgo(job.updated_at)}
      </div>
    </div>
  )
}

/* ─── Card grid view ─────────────────────────────────────── */

function CardGrid({ rows }: { rows: JobPostingSummary[] }) {
  return (
    <div
      className="grid gap-3"
      style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))' }}
    >
      {rows.map((j) => (
        <JobCard key={j.id} job={j} />
      ))}
    </div>
  )
}

function JobCard({ job }: { job: JobPostingSummary }) {
  return (
    <Link
      href={`/jobs/${job.id}`}
      className="flex min-h-[160px] cursor-pointer flex-col gap-2.5 rounded-[10px] border p-4 transition-shadow"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <div className="flex items-start gap-2.5">
        <div className="min-w-0 flex-1">
          <div
            className="flex items-center gap-2 truncate text-[14.5px] font-semibold"
            style={{ color: 'var(--px-fg)', lineHeight: 1.3 }}
          >
            <span className="truncate">{job.title}</span>
            <SourceChip source={job.source} />
            <NotSetUpChip job={job} />
          </div>
          <div className="mt-0.5 truncate text-[11.5px]" style={{ color: 'var(--px-fg-4)' }}>
            {job.org_unit_name ?? '—'}
          </div>
        </div>
        <StatusPill job={job} />
      </div>

      {job.needs_review_count > 0 ? (
        <div
          className="inline-flex items-center gap-1.5 text-[11.5px]"
          style={{ color: 'var(--px-accent)' }}
        >
          <SparkIcon size={10} />
          {job.needs_review_count} signal
          {job.needs_review_count === 1 ? '' : 's'} to double-check
        </div>
      ) : (
        <div
          className="text-[11.5px] italic"
          style={{ color: 'var(--px-fg-4)' }}
        >
          {job.signal_count > 0
            ? `${job.signal_count} signals ready`
            : 'No signals yet — Copilot can extract some from the JD.'}
        </div>
      )}

      <div className="flex-1" />

      <div
        className="flex items-center gap-2.5 border-t pt-2.5 text-[11.5px]"
        style={{ borderColor: 'var(--px-hairline)', color: 'var(--px-fg-3)' }}
      >
        <span>
          <b
            className="px-mono"
            style={{ color: 'var(--px-fg)', fontVariantNumeric: 'tabular-nums' }}
          >
            {job.signal_count}
          </b>{' '}
          signals
        </span>
        <div className="flex-1" />
        <span style={{ color: 'var(--px-fg-4)' }}>{postedAgo(job.updated_at)}</span>
      </div>
    </Link>
  )
}

/* ─── Kanban stub ────────────────────────────────────────── */

function KanbanStub() {
  return (
    <div
      className="rounded-[10px] border border-dashed p-10 text-center text-[13px]"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
        color: 'var(--px-fg-3)',
      }}
    >
      <div
        className="px-serif mb-2 text-2xl"
        style={{ color: 'var(--px-fg-2)' }}
      >
        Cross-role kanban
      </div>
      <div>Roles grouped by stage activity — coming next phase.</div>
    </div>
  )
}

/* ─── Sync progress dialog ───────────────────────────────── */

function SyncJobsProgressDialog({
  open,
  onOpenChange,
  log,
  isRunning,
  isStarting,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  log: ATSSyncLog | undefined
  isRunning: boolean
  /** True between mutation success and the first 'running' log poll — the
   * worker hasn't committed Phase A yet, so the dialog body should say
   * "Starting…" instead of falling back to the latest (stale) completed
   * log's counts. */
  isStarting: boolean
}) {
  // Same accessor shape as the page-level peek above: typed-cast through
  // unknown since the new orchestrator's `progress` shape is opaque
  // (Record<string, unknown>) and not populated during the run.
  const jobsProgress: { processed: number; total: number } | undefined =
    (log?.progress as Record<string, unknown> | undefined)?.['jobs'] as
      | { processed: number; total: number }
      | undefined
  // Flat counter map on the new orchestrator. Older logs (pre-cutover) may
  // still carry the nested {jobs: {new, updated, …}} shape — try both.
  const counts = log?.entity_counts as Record<string, unknown> | undefined
  const nestedJobs = counts?.['jobs'] as
    | { new?: number; updated?: number; skipped?: number; errors?: number }
    | undefined
  const jobsCounts = nestedJobs
    ? {
        new: nestedJobs.new ?? 0,
        updated: nestedJobs.updated ?? 0,
        skipped: nestedJobs.skipped ?? 0,
        errors: nestedJobs.errors ?? 0,
      }
    : counts
      ? {
          new: (counts['jobs_imported'] as number | undefined) ?? 0,
          updated: (counts['jobs_updated'] as number | undefined) ?? 0,
          skipped: (counts['jobs_unchanged'] as number | undefined) ?? 0,
          errors: (counts['jobs_errored'] as number | undefined) ?? 0,
        }
      : undefined

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {isStarting ? (
          <>
            <DialogTitle>Starting sync…</DialogTitle>
            <DialogDescription>
              The job is queued. Waiting for the worker to pick it up —
              this usually takes a couple of seconds.
            </DialogDescription>
            <div className="py-2">
              <SyncProgressBar processed={0} total={-1} />
            </div>
          </>
        ) : isRunning ? (
          <>
            <DialogTitle>Syncing jobs from ATS…</DialogTitle>
            <DialogDescription>
              Ceipal returns ~50 jobs per page; we also fetch per-job
              details for client linkage, so this can take a couple of
              minutes.
            </DialogDescription>
            <div className="py-2">
              {jobsProgress && jobsProgress.total > 0 ? (
                <SyncProgressBar
                  processed={jobsProgress.processed}
                  total={jobsProgress.total}
                />
              ) : (
                // The new orchestrator writes total = -1 (it doesn't know
                // the denominator upfront — Ceipal's iterator is a stream)
                // but DOES tick `processed` after every job. Pass it
                // through so the SyncProgressBar shows the live counter
                // instead of a frozen "Starting…" label.
                <SyncProgressBar
                  processed={jobsProgress?.processed ?? 0}
                  total={-1}
                />
              )}
            </div>
          </>
        ) : (
          <>
            <DialogTitle>
              {log?.status === 'failed'
                ? 'Sync failed'
                : log?.status === 'partial'
                  ? 'Sync partial'
                  : 'Sync complete'}
            </DialogTitle>
            <DialogDescription>
              {log?.status === 'failed' ? (
                <>The sync ran into an error and stopped. See details below.</>
              ) : jobsCounts == null ? (
                <>The jobs phase did not run on this sync.</>
              ) : jobsCounts.new === 0 &&
                jobsCounts.updated === 0 &&
                jobsCounts.skipped === 0 ? (
                <>
                  Already up to date — no new or modified jobs in Ceipal since
                  the last sync.
                </>
              ) : (
                <>
                  <span className="font-medium">{jobsCounts.new}</span> new,{' '}
                  <span className="font-medium">{jobsCounts.updated}</span>{' '}
                  updated
                  {jobsCounts.skipped > 0 && (
                    <>
                      , <span className="font-medium">{jobsCounts.skipped}</span>{' '}
                      skipped
                    </>
                  )}
                  {jobsCounts.errors > 0 && (
                    <>
                      ,{' '}
                      <span
                        className="font-medium"
                        style={{ color: 'var(--px-danger)' }}
                      >
                        {jobsCounts.errors} errors
                      </span>
                    </>
                  )}
                  .
                </>
              )}
            </DialogDescription>
            {log?.error_summary && (
              <p
                className="rounded-md border p-3 text-xs"
                style={{
                  color: 'var(--px-danger)',
                  borderColor: 'var(--px-danger)',
                  background: 'var(--px-surface-2)',
                }}
              >
                {log.error_summary}
              </p>
            )}
          </>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {isRunning || isStarting ? 'Hide' : 'Close'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/* ─── Empty state ────────────────────────────────────────── */

function EmptyState() {
  return (
    <div
      className="rounded-[10px] border p-12 text-center"
      style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
    >
      <h2
        className="px-serif m-0 mb-2 text-xl"
        style={{ color: 'var(--px-fg)' }}
      >
        No roles yet
      </h2>
      <p
        className="mx-auto mb-6 max-w-md text-sm"
        style={{ color: 'var(--px-fg-3)' }}
      >
        Paste a job description and Copilot will extract structured interview signals for you.
      </p>
      <Link
        href="/jobs/new"
        className="px-btn primary sm inline-block"
      >
        Create your first role
      </Link>
    </div>
  )
}
