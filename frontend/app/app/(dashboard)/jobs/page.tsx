'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Link from 'next/link'
import { useState } from 'react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  jobsApi,
  type JobPostingSummary,
  type JobStatus,
} from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

const STATUS_LABELS: Record<JobStatus, string> = {
  draft: 'Draft',
  signals_extracting: 'Extracting',
  signals_extraction_failed: 'Failed',
  signals_extracted: 'Ready',
  signals_confirmed: 'Confirmed',
}

const STATUS_VARIANT: Record<
  JobStatus,
  'default' | 'secondary' | 'destructive'
> = {
  draft: 'secondary',
  signals_extracting: 'secondary',
  signals_extraction_failed: 'destructive',
  signals_extracted: 'default',
  signals_confirmed: 'default',
}

export default function JobsListPage() {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const { data, isLoading, error } = useQuery<JobPostingSummary[]>({
    queryKey: ['jobs'],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.list(token)
    },
  })

  const deleteMutation = useMutation<void, Error, string[]>({
    mutationFn: async (ids) => {
      const token = await getFreshSupabaseToken()
      await Promise.all(ids.map((id) => jobsApi.delete(token, id)))
    },
    onSuccess: (_, ids) => {
      toast.success(`${ids.length} job(s) deleted`)
      setSelected(new Set())
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    },
    onError: (err) => {
      toast.error(`Delete failed: ${err.message}`)
    },
  })

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAll() {
    if (!data) return
    if (selected.size === data.length) {
      setSelected(new Set())
    } else {
      setSelected(new Set(data.map((j) => j.id)))
    }
  }

  function handleBulkDelete() {
    if (selected.size === 0) return
    const confirmed = window.confirm(
      `Delete ${selected.size} job description(s)? This cannot be undone.`,
    )
    if (!confirmed) return
    deleteMutation.mutate([...selected])
  }

  if (isLoading) {
    return <div className="text-sm text-zinc-500">Loading…</div>
  }
  if (error) {
    return (
      <div className="text-sm text-red-500">
        Error: {(error as Error).message}
      </div>
    )
  }

  const allSelected = !!data && data.length > 0 && selected.size === data.length

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-zinc-900">
          Job Descriptions
        </h1>
        <div className="flex items-center gap-3">
          {selected.size > 0 && (
            <Button
              variant="destructive"
              size="sm"
              disabled={deleteMutation.isPending}
              onClick={handleBulkDelete}
            >
              {deleteMutation.isPending
                ? 'Deleting…'
                : `Delete ${selected.size} selected`}
            </Button>
          )}
          <Link href="/jobs/new">
            <Button>+ New JD</Button>
          </Link>
        </div>
      </div>

      {!data || data.length === 0 ? (
        <div className="bg-white border border-zinc-200 rounded-lg p-12 text-center">
          <h2 className="text-lg font-semibold text-zinc-900 mb-2">
            No JDs yet
          </h2>
          <p className="text-sm text-zinc-500 mb-6">
            Paste a job description to generate structured interview signals.
          </p>
          <Link href="/jobs/new">
            <Button>Create your first JD</Button>
          </Link>
        </div>
      ) : (
        <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden">
          <table className="w-full">
            <thead className="bg-zinc-50 border-b border-zinc-200">
              <tr>
                <th className="w-10 px-4 py-3">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={toggleAll}
                    className="rounded border-zinc-300"
                    aria-label="Select all jobs"
                  />
                </th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">
                  Title
                </th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">
                  Unit
                </th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">
                  Status
                </th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">
                  Created by
                </th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">
                  Last updated
                </th>
              </tr>
            </thead>
            <tbody>
              {data.map((job) => (
                <tr
                  key={job.id}
                  className={`border-b border-zinc-100 hover:bg-zinc-50 ${
                    selected.has(job.id) ? 'bg-blue-50/50' : ''
                  }`}
                >
                  <td className="w-10 px-4 py-3">
                    <input
                      type="checkbox"
                      checked={selected.has(job.id)}
                      onChange={() => toggleSelect(job.id)}
                      className="rounded border-zinc-300"
                      aria-label={`Select ${job.title}`}
                    />
                  </td>
                  <td className="px-4 py-3">
                    <Link
                      href={`/jobs/${job.id}`}
                      className="text-sm font-medium text-blue-600 hover:underline"
                    >
                      {job.title}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-sm text-zinc-600">
                    {job.org_unit_name ?? '—'}
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={STATUS_VARIANT[job.status]}>
                      {STATUS_LABELS[job.status]}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-xs text-zinc-500">
                    {job.created_by_email ?? '—'}
                  </td>
                  <td className="px-4 py-3">
                    <div className="text-xs text-zinc-500 whitespace-nowrap">
                      {new Date(job.updated_at).toLocaleDateString()}
                    </div>
                    {job.updated_by_email && (
                      <div className="text-xs text-zinc-400">
                        by {job.updated_by_email}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
