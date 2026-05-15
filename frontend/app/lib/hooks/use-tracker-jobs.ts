'use client'

import { useQuery } from '@tanstack/react-query'

import { jobsApi, type JobPostingSummary } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/**
 * Live jobs for the Tracker landing page — anything tagged "live" by the
 * /jobs page (`signals_confirmed`, `pipeline_built`, or `active`), sorted
 * by `updated_at` desc. Reuses the `['jobs-list']` cache so visiting /jobs
 * and /tracker doesn't double-fetch. The kanban view itself handles the
 * empty-pipeline case ("This role has no pipeline stages…") so jobs in
 * `signals_confirmed` still surface — they're just an obvious nudge to
 * build the pipeline.
 */
const LIVE_STATUSES: ReadonlySet<JobPostingSummary['status']> = new Set([
  'signals_confirmed',
  'pipeline_built',
  'active',
])

export function useTrackerJobs() {
  return useQuery<JobPostingSummary[]>({
    queryKey: ['jobs-list'],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.list(token, undefined, { signal })
    },
    select: (jobs) =>
      jobs
        .filter((j) => LIVE_STATUSES.has(j.status))
        .sort(
          (a, b) =>
            new Date(b.updated_at).getTime() -
              new Date(a.updated_at).getTime() ||
            // Tiebreak on id so ATS bulk imports (which can land with
            // identical updated_at) don't flicker between renders.
            a.id.localeCompare(b.id),
        ),
  })
}
