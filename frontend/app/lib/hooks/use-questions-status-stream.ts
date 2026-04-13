'use client'

import { fetchEventSource } from '@microsoft/fetch-event-source'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import { getFreshSupabaseToken } from '@/lib/auth/tokens'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'

/**
 * Opens an SSE connection to /api/jobs/{id}/pipeline/questions/status-stream
 * and invalidates the relevant TanStack Query caches on every event.
 *
 * All events invalidate the per-job banks overview. Bank-level events
 * (`bank.status_changed`, `bank.question_updated`) additionally invalidate
 * the detail cache for the currently-selected stage, if any.
 *
 * Note: this hook intentionally uses a lightweight handler and relies on
 * fetch-event-source's built-in auto-retry for transient failures. A more
 * elaborate reconnect-with-fresh-token flow (see useJobStatusStream) can be
 * added if token expiry during long-lived streams becomes a problem in
 * practice.
 */
export function useQuestionsStatusStream(
  jobId: string,
  selectedStageId: string | null,
) {
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!jobId) return

    const controller = new AbortController()

    const run = async () => {
      let token: string
      try {
        token = await getFreshSupabaseToken()
      } catch {
        // No session — caller is probably redirecting to login.
        return
      }
      if (controller.signal.aborted) return

      try {
        await fetchEventSource(
          `${API_URL}/api/jobs/${jobId}/pipeline/questions/status-stream`,
          {
            method: 'GET',
            headers: { Authorization: `Bearer ${token}` },
            signal: controller.signal,
            onmessage(ev) {
              // All events invalidate the per-job banks overview.
              void queryClient.invalidateQueries({
                queryKey: ['banks', jobId],
              })
              // Bank-level events also invalidate the selected bank detail.
              if (
                (ev.event === 'bank.status_changed' ||
                  ev.event === 'bank.question_updated') &&
                selectedStageId
              ) {
                void queryClient.invalidateQueries({
                  queryKey: ['bank', jobId, selectedStageId],
                })
              }
            },
            onerror(err) {
              // Let fetch-event-source auto-retry on transient errors.
              console.error('Questions SSE error:', err)
            },
          },
        )
      } catch {
        // Swallow — typically AbortError on unmount.
      }
    }

    void run()
    return () => controller.abort()
  }, [jobId, selectedStageId, queryClient])
}
