'use client'

import { useQuery } from '@tanstack/react-query'
import Link from 'next/link'

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
}

const STATUS_VARIANT: Record<
  JobStatus,
  'default' | 'secondary' | 'destructive'
> = {
  draft: 'secondary',
  signals_extracting: 'secondary',
  signals_extraction_failed: 'destructive',
  signals_extracted: 'default',
}

export default function JobsListPage() {
  const { data, isLoading, error } = useQuery<JobPostingSummary[]>({
    queryKey: ['jobs'],
    queryFn: async () => {
      const token = await getFreshSupabaseToken()
      return jobsApi.list(token)
    },
  })

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

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-zinc-900">
          Job Descriptions
        </h1>
        <Link href="/jobs/new">
          <Button>+ New JD</Button>
        </Link>
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
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">
                  Title
                </th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">
                  Status
                </th>
                <th className="text-left text-xs font-semibold uppercase tracking-wide text-zinc-500 px-4 py-3">
                  Created
                </th>
              </tr>
            </thead>
            <tbody>
              {data.map((job) => (
                <tr
                  key={job.id}
                  className="border-b border-zinc-100 hover:bg-zinc-50"
                >
                  <td className="px-4 py-3">
                    <Link
                      href={`/jobs/${job.id}`}
                      className="text-sm font-medium text-blue-600 hover:underline"
                    >
                      {job.title}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={STATUS_VARIANT[job.status]}>
                      {STATUS_LABELS[job.status]}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-xs text-zinc-500">
                    {new Date(job.created_at).toLocaleString()}
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
