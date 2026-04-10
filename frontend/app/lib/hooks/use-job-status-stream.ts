'use client'

import { EventStreamContentType, fetchEventSource } from '@microsoft/fetch-event-source'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { type JobStatusEvent } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000'

/** Max times we'll re-fetch a token and reconnect on auth failure before
 *  giving up. Protects against loops when the refresh token is also expired. */
const MAX_AUTH_RETRIES = 2

/** Thrown when the server returns a non-retryable client error (401/403).
 *  fetch-event-source retries when onerror doesn't throw; we throw this to
 *  break OUT of the library's retry loop so we can reconnect with a fresh
 *  token at the outer level. */
class AuthSSEError extends Error {}

/** Non-auth fatal client errors (404, etc.) — stop permanently. */
class FatalSSEError extends Error {}

type StreamResult = {
  status: JobStatusEvent | null
  /** Non-null when the SSE connection failed. The page still works
   *  (TanStack Query polls), but live updates are unavailable. */
  error: string | null
}

/**
 * Opens an SSE connection to /api/jobs/{id}/status/stream and updates
 * local state + the TanStack Query cache on every status event.
 *
 * Token lifecycle:
 *  - A fresh Supabase token is fetched before EACH connection attempt.
 *  - On 401 (expired token), the hook breaks out of fetch-event-source's
 *    internal retry loop, fetches a new token via getFreshSupabaseToken()
 *    (which auto-refreshes via the refresh token cookie), and reconnects.
 *  - After MAX_AUTH_RETRIES consecutive auth failures (meaning the refresh
 *    token is also expired), it gives up with a permanent error.
 *  - On successful connection, the auth retry counter resets to 0.
 */
export function useJobStatusStream(jobId: string): StreamResult {
  const [status, setStatus] = useState<JobStatusEvent | null>(null)
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  useEffect(() => {
    if (!jobId) return

    const ctrl = new AbortController()
    let authRetries = 0

    async function connect(): Promise<void> {
      if (ctrl.signal.aborted) return

      // ---- 1. Fetch a fresh token for this connection attempt ----
      let token: string
      try {
        token = await getFreshSupabaseToken()
      } catch {
        if (!ctrl.signal.aborted) {
          setError('Session expired — please log in again.')
        }
        return
      }
      if (ctrl.signal.aborted) return

      // ---- 2. Open SSE with the fresh token ----
      try {
        await fetchEventSource(
          `${API_URL}/api/jobs/${jobId}/status/stream`,
          {
            signal: ctrl.signal,
            headers: { Authorization: `Bearer ${token}` },

            async onopen(response) {
              if (
                response.ok &&
                response.headers
                  .get('content-type')
                  ?.includes(EventStreamContentType)
              ) {
                // Connection succeeded — reset auth retry counter.
                authRetries = 0
                setError(null)
                return
              }
              // 401/403 → break out so we can reconnect with a fresh token.
              if (response.status === 401 || response.status === 403) {
                throw new AuthSSEError()
              }
              // Other 4xx (except 429) → permanent failure, no point retrying.
              if (
                response.status >= 400 &&
                response.status < 500 &&
                response.status !== 429
              ) {
                throw new FatalSSEError(
                  `SSE connection refused (${response.status}).`,
                )
              }
              // 5xx / 429 → transient, let onerror handle (auto-retry).
              throw new Error(`SSE server error: ${response.status}`)
            },

            onmessage(ev) {
              try {
                const payload = JSON.parse(ev.data) as JobStatusEvent
                setStatus(payload)
                setError(null)
                queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
              } catch {
                // Empty heartbeat events or malformed JSON — ignore.
              }
            },

            onerror(err) {
              // Auth / fatal errors: throw to break out of library's retry loop.
              // Our outer catch block handles the reconnection logic.
              if (err instanceof AuthSSEError || err instanceof FatalSSEError) {
                throw err
              }
              // Transient errors: don't throw → library auto-retries with backoff.
              // If the token expires during this internal retry, the next attempt
              // will get 401 → onopen throws AuthSSEError → we break out and
              // reconnect with a fresh token at the outer level.
              console.warn('SSE transient error', err)
              setError(
                'Live updates unavailable — page will refresh automatically.',
              )
            },
          },
        )
      } catch (err) {
        if (ctrl.signal.aborted) return

        // ---- 3a. Auth failure → re-fetch token and reconnect ----
        if (err instanceof AuthSSEError) {
          authRetries++
          if (authRetries <= MAX_AUTH_RETRIES) {
            return connect()
          }
          // Exhausted retries — refresh token is also expired.
          setError('Session expired — please log in again.')
          return
        }

        // ---- 3b. Fatal client error → stop permanently ----
        if (err instanceof FatalSSEError) {
          setError(err.message)
          return
        }

        // ---- 3c. Unexpected error ----
        console.warn('SSE connection failed', err)
        setError('Live updates unavailable — page will refresh automatically.')
      }
    }

    connect()
    return () => ctrl.abort()
  }, [jobId, queryClient])

  return { status, error }
}
