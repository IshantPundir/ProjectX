'use client'

import { fetchEventSource } from '@microsoft/fetch-event-source'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { type JobStatusEvent } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'

/**
 * Opens an SSE connection to /api/jobs/{id}/status/stream and updates
 * local state + the TanStack Query cache on every status event.
 *
 * IMPORTANT: the Supabase token must be fetched BEFORE opening the SSE
 * connection. `await` cannot be used inside a sync object literal, so we
 * use a .then() chain to fetch first, then pass the token into fetchEventSource.
 */
export function useJobStatusStream(jobId: string) {
  const [status, setStatus] = useState<JobStatusEvent | null>(null)
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!jobId) return

    const ctrl = new AbortController()

    getFreshSupabaseToken()
      .then((token) => {
        if (ctrl.signal.aborted) return
        fetchEventSource(`${API_URL}/api/jobs/${jobId}/status/stream`, {
          signal: ctrl.signal,
          headers: { Authorization: `Bearer ${token}` },
          onmessage(ev) {
            try {
              const payload = JSON.parse(ev.data) as JobStatusEvent
              setStatus(payload)
              queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
            } catch (e) {
              console.warn('SSE parse error', e)
            }
          },
          onerror(err) {
            // fetch-event-source auto-retries with backoff; don't throw unless fatal.
            console.warn('SSE error', err)
          },
        }).catch((err) => {
          console.warn('SSE connection failed', err)
        })
      })
      .catch((err) => {
        console.warn('Failed to get token for SSE', err)
      })

    return () => ctrl.abort()
  }, [jobId, queryClient])

  return status
}
