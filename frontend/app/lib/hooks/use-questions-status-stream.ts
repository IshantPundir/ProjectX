'use client'

import {
  EventStreamContentType,
  fetchEventSource,
} from '@microsoft/fetch-event-source'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

import { getFreshSupabaseToken } from '@/lib/auth/tokens'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'

/** Max times we'll re-fetch a token and reconnect on auth failure. */
const MAX_AUTH_RETRIES = 2
/** Absolute reconnection ceiling — protects against runaway loops. */
const MAX_TOTAL_RETRIES = 20

class AuthSSEError extends Error {}
class FatalSSEError extends Error {}

/**
 * Opens an SSE connection to /api/jobs/{id}/pipeline/questions/status-stream
 * and invalidates TanStack Query caches on every event.
 *
 * Token lifecycle mirrors useJobStatusStream:
 *  - Fresh token fetched before each connection attempt.
 *  - On 401 → break out of fetch-event-source's internal retry, refresh
 *    via getFreshSupabaseToken (which uses the cookie refresh token),
 *    reconnect.
 *  - After MAX_AUTH_RETRIES the hook gives up.
 *  - Every onerror counts against MAX_TOTAL_RETRIES — runaway transient
 *    retry storms cannot occur.
 */
export function useQuestionsStatusStream(
  jobId: string,
  selectedStageId: string | null,
) {
  const queryClient = useQueryClient()

  const selectedStageIdRef = useRef(selectedStageId)
  useEffect(() => {
    selectedStageIdRef.current = selectedStageId
  }, [selectedStageId])

  useEffect(() => {
    if (!jobId) return

    const ctrl = new AbortController()
    let authRetries = 0
    let totalRetries = 0

    async function connect(): Promise<void> {
      if (ctrl.signal.aborted) return

      let token: string
      try {
        token = await getFreshSupabaseToken()
      } catch {
        // Caller is probably redirecting to login.
        return
      }
      if (ctrl.signal.aborted) return

      try {
        await fetchEventSource(
          `${API_URL}/api/jobs/${jobId}/pipeline/questions/status-stream`,
          {
            method: 'GET',
            headers: { Authorization: `Bearer ${token}` },
            signal: ctrl.signal,

            async onopen(response) {
              if (
                response.ok &&
                response.headers
                  .get('content-type')
                  ?.includes(EventStreamContentType)
              ) {
                authRetries = 0
                return
              }
              if (response.status === 401 || response.status === 403) {
                throw new AuthSSEError()
              }
              if (
                response.status >= 400 &&
                response.status < 500 &&
                response.status !== 429
              ) {
                throw new FatalSSEError(
                  `SSE connection refused (${response.status}).`,
                )
              }
              throw new Error(`SSE server error: ${response.status}`)
            },

            onmessage(ev) {
              void queryClient.invalidateQueries({
                queryKey: ['banks', jobId],
              })
              const currentStageId = selectedStageIdRef.current
              if (
                (ev.event === 'bank.status_changed' ||
                  ev.event === 'bank.question_updated' ||
                  ev.event === 'bank.question_added') &&
                currentStageId
              ) {
                void queryClient.invalidateQueries({
                  queryKey: ['bank', jobId, currentStageId],
                })
              }
            },

            onerror(err) {
              if (
                err instanceof AuthSSEError ||
                err instanceof FatalSSEError
              ) {
                throw err
              }
              totalRetries++
              if (totalRetries > MAX_TOTAL_RETRIES) {
                throw new FatalSSEError(
                  'Live updates unavailable — reconnection limit reached.',
                )
              }
              console.warn('Questions SSE transient error', err)
            },
          },
        )
      } catch (err) {
        if (ctrl.signal.aborted) return

        if (err instanceof AuthSSEError) {
          authRetries++
          totalRetries++
          if (
            authRetries <= MAX_AUTH_RETRIES &&
            totalRetries <= MAX_TOTAL_RETRIES
          ) {
            return connect()
          }
          return
        }

        if (err instanceof FatalSSEError) {
          console.warn('Questions SSE fatal:', err.message)
          return
        }

        console.warn('Questions SSE connection failed', err)
      }
    }

    void connect()
    return () => ctrl.abort()
  }, [jobId, queryClient])
}
