'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { Button } from '@/components/px'
import { VerdictChip } from '@/components/dashboard/reports/VerdictBand'
import { formatTen } from '@/components/dashboard/reports/report-format'
import type { ReportIndexItem } from '@/lib/api/reports'
import { useMe } from '@/lib/hooks/use-me'
import { useRegenerateReport, useReportsIndex } from '@/lib/hooks/use-report'

const STATUS_LABEL: Record<ReportIndexItem['report_status'], string> = {
  none: 'Not generated',
  pending: 'Generating…',
  generating: 'Generating…',
  ready: 'Ready',
  failed: 'Failed',
}

function reportHref(item: ReportIndexItem): string {
  const p = new URLSearchParams()
  if (item.candidate_id) p.set('candidateId', item.candidate_id)
  if (item.candidate_name) p.set('candidateName', item.candidate_name)
  if (item.job_title) p.set('title', item.job_title)
  if (item.stage_name) p.set('subtitle', item.stage_name)
  return `/reports/session/${item.session_id}?${p.toString()}`
}

export default function ReportsPage() {
  const { data, isLoading, error } = useReportsIndex()
  const { data: me } = useMe()
  const isSuperAdmin = !!me?.is_super_admin
  const [sortByScore, setSortByScore] = useState(false)
  const items = (data?.items ?? []).slice().sort((a, b) =>
    sortByScore ? (b.overall_score ?? -1) - (a.overall_score ?? -1) : 0,
  )

  return (
    <div className="mx-auto max-w-[1200px] px-8 pb-10 pt-5">
      <div className="mb-6">
        <h1
          className="px-serif m-0 text-[30px] font-normal"
          style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
        >
          Reports
        </h1>
        <p className="mt-2 text-[13px]" style={{ color: 'var(--px-fg-3)', lineHeight: 1.6 }}>
          Completed AI interviews and their evaluations. Open a report, or
          generate one for a session that hasn&rsquo;t been scored yet.
        </p>
      </div>

      {isLoading ? (
        <div className="text-sm" style={{ color: 'var(--px-fg-3)' }}>Loading…</div>
      ) : error ? (
        <div className="text-sm" style={{ color: 'var(--px-danger)' }}>
          Could not load reports.
        </div>
      ) : !data || data.items.length === 0 ? (
        <div
          className="rounded-[10px] border p-8 text-center"
          style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
        >
          <p className="text-sm" style={{ color: 'var(--px-fg-3)' }}>No completed interviews yet.</p>
          <p className="mt-1 text-xs" style={{ color: 'var(--px-fg-4)' }}>
            Reports appear here once an AI-screening interview completes.
          </p>
        </div>
      ) : (
        <div
          className="overflow-hidden rounded-[10px] border"
          style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
        >
          <table className="min-w-full text-left text-[13px]">
            <thead>
              <tr style={{ background: 'var(--px-surface-2)', color: 'var(--px-fg-4)' }}>
                <th className="px-4 py-2.5 text-[10.5px] font-semibold uppercase tracking-wide">Candidate</th>
                <th className="px-4 py-2.5 text-[10.5px] font-semibold uppercase tracking-wide">Role</th>
                <th className="px-4 py-2.5 text-[10.5px] font-semibold uppercase tracking-wide">Stage</th>
                <th className="px-4 py-2.5 text-[10.5px] font-semibold uppercase tracking-wide">Verdict</th>
                <th className="px-4 py-2.5 text-right text-[10.5px] font-semibold uppercase tracking-wide">
                  <button type="button" onClick={() => setSortByScore((v) => !v)}
                          aria-label="Sort by score"
                          className="uppercase tracking-wide hover:underline"
                          style={{ color: sortByScore ? 'var(--px-accent)' : 'inherit' }}>
                    Score {sortByScore ? '↓' : ''}
                  </button>
                </th>
                <th className="px-4 py-2.5 text-right text-[10.5px] font-semibold uppercase tracking-wide">Action</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <ReportRow key={item.session_id} item={item} isSuperAdmin={isSuperAdmin} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function ReportRow({ item, isSuperAdmin }: { item: ReportIndexItem; isSuperAdmin: boolean }) {
  const qc = useQueryClient()
  const regen = useRegenerateReport(item.session_id)
  const hasReport = item.report_status === 'ready' || item.report_status === 'failed'
  const generating = item.report_status === 'pending' || item.report_status === 'generating'
  const ten = formatTen(item.overall_score)

  const handleGenerate = () => {
    regen.mutate(undefined, {
      onSuccess: () => {
        toast.success('Report generation started')
        void qc.invalidateQueries({ queryKey: ['reports-index'] })
      },
      onError: (e) => toast.error(e.message || 'Could not start generation'),
    })
  }

  return (
    <tr className="border-t" style={{ borderColor: 'var(--px-hairline)' }}>
      <td className="px-4 py-2.5" style={{ color: 'var(--px-fg)' }}>{item.candidate_name ?? '—'}</td>
      <td className="px-4 py-2.5" style={{ color: 'var(--px-fg-2)' }}>{item.job_title ?? '—'}</td>
      <td className="px-4 py-2.5" style={{ color: 'var(--px-fg-3)' }}>{item.stage_name ?? '—'}</td>
      <td className="px-4 py-2.5">
        {item.verdict ? <VerdictChip verdict={item.verdict} /> : <span style={{ color: 'var(--px-fg-4)' }}>—</span>}
      </td>
      <td className="px-4 py-2.5 text-right tabular-nums" style={{ color: 'var(--px-fg-2)' }}>{ten ?? '—'}</td>
      <td className="px-4 py-2.5 text-right">
        {hasReport || generating ? (
          <Link href={reportHref(item)} className="text-[12px] font-medium hover:underline" style={{ color: 'var(--px-accent)' }}>
            {generating ? 'Generating…' : 'View report'}
          </Link>
        ) : isSuperAdmin ? (
          <Button
            type="button"
            variant="outline"
            size="xs"
            loading={regen.isPending}
            disabled={regen.isPending}
            onClick={handleGenerate}
          >
            Generate
          </Button>
        ) : (
          <span className="text-[12px]" style={{ color: 'var(--px-fg-4)' }}>{STATUS_LABEL[item.report_status]}</span>
        )}
      </td>
    </tr>
  )
}
