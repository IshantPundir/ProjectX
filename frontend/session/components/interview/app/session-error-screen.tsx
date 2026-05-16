'use client'

import { copyForErrorCode } from '../lib/session-error-messages'

interface Props {
  errorCode: string | null
  sessionId: string
}

/**
 * Terminal error screen shown when a session ends in state='error'.
 *
 * Two information paths into this screen:
 *   1. LK `session_outcome='error'` attribute (real-time, no code).
 *   2. HTTP `/state` poll showing state='error' (carries the error_code).
 *
 * Path 1 renders with errorCode=null and falls back to generic copy.
 * Path 2 renders with the full code and shows code-specific copy.
 *
 * No retry button — recruiter-driven retry per the failure-handling
 * spec (2026-05-16). The recruiter sees the failure on their tracker
 * and resends the invite using the existing scheduler flow.
 */
export function SessionErrorScreen({ errorCode, sessionId }: Props) {
  const { headline, body } = copyForErrorCode(errorCode)

  return (
    <div className="min-h-screen grid place-items-center bg-zinc-50 px-6">
      <div className="max-w-md text-center">
        <h1 className="text-2xl font-semibold text-zinc-900">
          {headline}
        </h1>
        <p className="mt-3 text-sm text-zinc-600">
          {body}
        </p>
        <p className="mt-6 text-xs text-zinc-500">
          You can close this window. If you need help, reach out to your
          recruiter and include this reference:{' '}
          <span className="font-mono">{sessionId}</span>.
        </p>
      </div>
    </div>
  )
}
