/**
 * Candidate-facing copy for each backend error_code.
 *
 * Backend taxonomy lives in
 * `backend/nexus/app/modules/session/error_codes.py::ErrorCode`. Adding a
 * code there requires adding an entry here in the same PR — the FALLBACK
 * below is the safety net for forward-compat (backend rolls a new code
 * before this frontend ships support).
 */

interface ErrorCopy {
  headline: string
  body: string
}

export const SESSION_ERROR_COPY: Record<string, ErrorCopy> = {
  engine_session_config_invalid: {
    headline: 'We hit a configuration issue',
    body: "Your interview couldn't be set up correctly. Your recruiter has been notified and will send a new invite.",
  },
  engine_company_profile_missing: {
    headline: "Your interview isn't fully set up",
    body: 'Some company information is missing. Your recruiter will reach out shortly.',
  },
  engine_question_bank_not_ready: {
    headline: "Your interview isn't fully set up",
    body: "The questions for this interview aren't ready yet. Your recruiter will reach out shortly.",
  },
  engine_room_join_failed: {
    headline: 'Something went wrong on our side',
    body: "We couldn't connect to your interview room. Your recruiter will resend the invite.",
  },
  engine_internal_error: {
    headline: 'Something went wrong on our side',
    body: 'Your recruiter has been notified and will resend the invite.',
  },
  engine_unresponsive: {
    headline: "Your interview didn't start",
    body: 'The interview was abandoned without progress. Your recruiter will reach out to reschedule.',
  },
}

const FALLBACK: ErrorCopy = {
  headline: 'Something went wrong',
  body: 'Your recruiter will be in touch with next steps.',
}

export function copyForErrorCode(code: string | null | undefined): ErrorCopy {
  if (!code) return FALLBACK
  return SESSION_ERROR_COPY[code] ?? FALLBACK
}
