'use client'

import { useQuery } from '@tanstack/react-query'

import { jobsApi, type JobPostingSummary } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/**
 * Live roles for the Tracker landing page — `active` only. Roles in
 * `signals_confirmed`/`pipeline_built` ("In review") are excluded
 * deliberately: they can't accept candidates yet, so surfacing them on
 * a candidate-pipeline tracker is misleading. Reuses the `['jobs-list']`
 * cache so visiting /jobs and /tracker doesn't double-fetch.
 */
export function useTrackerJobs() {
  return useQuery<JobPostingSummary[]>({
    queryKey: ['jobs-list'],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.list(token, undefined, { signal })
    },
    select: (jobs) =>
      jobs
        .filter((j) => j.status === 'active')
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
