'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'

import {
  Button,
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/px'
import {
  jobsApi,
  type JobPostingSummary,
  type JobStatus,
} from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/* ─── Status → design-system chip ─────────────────────────── */

type StatusKind = 'reviewing' | 'draft' | 'live' | 'failed'

function statusKind(s: JobStatus): StatusKind {
  if (s === 'signals_extracted' || s === 'signals_extracting') return 'reviewing'
  if (s === 'signals_extraction_failed') return 'failed'
  if (s === 'draft') return 'draft'
  return 'live'
}

function StatusPill({ status }: { status: JobStatus }) {
  const kind = statusKind(status)
  const map: Record<StatusKind, { label: string; cls: string }> = {
    reviewing: { label: 'reviewing', cls: 'caution' },
    draft: { label: 'draft', cls: 'soft' },
    live: { label: 'live', cls: 'ok' },
    failed: { label: 'failed', cls: 'danger' },
  }
  const m = map[kind]
  return (
    <span className={`px-chip ${m.cls} h-5 text-[10.5px] font-medium tracking-wide`}>
      <span className="px-dot" />
      {m.label}
    </span>
  )
}

/* ─── Posted-ago helper ───────────────────────────────────── */

function postedAgo(iso: string): string {
  const now = Date.now()
  const then = new Date(iso).getTime()
  const days = Math.floor((now - then) / (1000 * 60 * 60 * 24))
  if (days === 0) return 'today'
  if (days === 1) return '1d ago'
  if (days < 30) return `${days}d ago`
  const months = Math.floor(days / 30)
  if (months === 1) return '1mo ago'
  return `${months}mo ago`
}

/* ─── Grouping logic ──────────────────────────────────────── */

const IDLE_DAYS = 7
type Group = 'needs_you' | 'in_motion' | 'quiet'

function classifyJob(job: JobPostingSummary): Group {
  if (
    job.status === 'draft' ||
    job.status === 'signals_extracted' ||
    job.status === 'signals_extraction_failed'
  ) {
    return 'needs_you'
  }
  const updated = new Date(job.updated_at).getTime()
  const ageDays = (Date.now() - updated) / (1000 * 60 * 60 * 24)
  if (ageDays > IDLE_DAYS) return 'quiet'
  return 'in_motion'
}

const GROUP_META: Record<Group, { heading: string; hint: string }> = {
  needs_you: { heading: 'Needs you', hint: 'awaiting your review or action' },
  in_motion: { heading: 'In motion', hint: 'candidates actively progressing' },
  quiet: { heading: 'Quiet', hint: `no activity in ${IDLE_DAYS}+ days` },
}

type FilterId = 'all' | 'needs_you' | 'in_motion' | 'quiet'
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

  const { data, isLoading, error } = useQuery<JobPostingSummary[]>({
    queryKey: ['jobs-list'],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.list(token, undefined, { signal })
    },
  })

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
      needs_you: [],
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
        needs_you: buckets.needs_you.length,
        in_motion: buckets.in_motion.length,
        quiet: buckets.quiet.length,
      },
    }
  }, [data])

  const visibleGroups = useMemo<Group[]>(() => {
    if (filter === 'all') return ['needs_you', 'in_motion', 'quiet']
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

        <Link
          href="/jobs/new"
          className="px-btn primary sm"
        >
          <PlusIcon size={12} />
          New role
        </Link>
      </div>

      {/* Filter pills + view switcher */}
      <div className="mb-3.5 flex items-center gap-1.5">
        {(
          [
            { id: 'all', label: 'All', n: counts.all },
            { id: 'needs_you', label: 'Needs you', n: counts.needs_you },
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
        <StatusPill status={job.status} />
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
            className="truncate text-[14.5px] font-semibold"
            style={{ color: 'var(--px-fg)', lineHeight: 1.3 }}
          >
            {job.title}
          </div>
          <div className="mt-0.5 truncate text-[11.5px]" style={{ color: 'var(--px-fg-4)' }}>
            {job.org_unit_name ?? '—'}
          </div>
        </div>
        <StatusPill status={job.status} />
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
