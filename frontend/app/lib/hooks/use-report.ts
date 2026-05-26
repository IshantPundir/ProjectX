'use client'

import { useRef } from 'react'
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'

import { ApiError } from '@/lib/api/client'
import {
  reportsApi,
  type HumanDecisionIn,
  type ReportEnvelope,
  type ReportRead,
} from '@/lib/api/reports'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export type ReportState =
  | { kind: 'loading' }
  | { kind: 'forbidden' }
  | { kind: 'noReport' }
  | { kind: 'pending' }
  | { kind: 'failed'; report: ReportRead }
  | { kind: 'ready'; report: ReportRead }

/** Pure mapping from query result → the state the page switches on. */
export function deriveReportState(q: {
  isLoading: boolean
  data: ReportEnvelope | undefined
  error: unknown
}): ReportState {
  if (q.error instanceof ApiError && q.error.status === 403) return { kind: 'forbidden' }
  if (q.data) {
    if (q.data.state === 'noReport') return { kind: 'noReport' }
    if (q.data.state === 'pending') return { kind: 'pending' }
    const r = q.data.report
    return r.status === 'failed' ? { kind: 'failed', report: r } : { kind: 'ready', report: r }
  }
  if (q.isLoading) return { kind: 'loading' }
  // Non-403 error with no data — surface as loading-failed via the route error boundary.
  if (q.error) throw q.error
  return { kind: 'loading' }
}

const GRACE_MS = 30_000 // poll through the 404 window right after a regenerate

export function useReport(sessionId: string) {
  // When a regenerate was just requested, keep polling even while the GET
  // still returns noReport (the actor hasn't created the row yet).
  const generatingUntilRef = useRef<number>(0)

  const query: UseQueryResult<ReportEnvelope> = useQuery<ReportEnvelope>({
    queryKey: ['report', sessionId],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return reportsApi.getBySession(token, sessionId, { signal })
    },
    enabled: !!sessionId,
    retry: (count, err) => {
      if (err instanceof ApiError && (err.status === 403 || err.status === 404)) return false
      return count < 2
    },
    refetchInterval: (q) => {
      const data = q.state.data
      if (data?.state === 'pending') return 4000
      if (data?.state === 'noReport' && Date.now() < generatingUntilRef.current) return 4000
      return false
    },
  })

  const state = deriveReportState({
    isLoading: query.isLoading,
    data: query.data,
    error: query.error,
  })

  return { state, query, markGenerating: () => { generatingUntilRef.current = Date.now() + GRACE_MS } }
}

export function useRecordDecision(sessionId: string) {
  const qc = useQueryClient()
  return useMutation<ReportRead, Error, { reportId: string; body: HumanDecisionIn }>({
    mutationFn: async ({ reportId, body }) => {
      const token = await getFreshSupabaseToken()
      return reportsApi.recordDecision(token, reportId, body)
    },
    onSuccess: (report) => {
      qc.setQueryData<ReportEnvelope>(['report', sessionId], { state: 'ready', report })
    },
  })
}

export function useRegenerateReport(sessionId: string) {
  const qc = useQueryClient()
  return useMutation<{ status: string }, Error, void>({
    mutationFn: async () => {
      const token = await getFreshSupabaseToken()
      return reportsApi.regenerate(token, sessionId)
    },
    onSuccess: () => {
      // Optimistically flip to pending so polling starts immediately.
      qc.setQueryData<ReportEnvelope>(['report', sessionId], { state: 'pending', status: 'generating' })
    },
  })
}
