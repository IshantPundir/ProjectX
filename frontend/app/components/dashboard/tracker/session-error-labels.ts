/**
 * Recruiter-facing labels for backend error_code values.
 *
 * Backend taxonomy: app/modules/session/error_codes.py::ErrorCode.
 * Adding a code there requires adding an entry here in the same PR.
 * Unknown codes fall back to "Failed" (forward-compat).
 */

export const SESSION_ERROR_LABELS: Record<string, string> = {
  engine_session_config_invalid: 'Configuration error',
  engine_company_profile_missing: 'Company profile incomplete',
  engine_question_bank_not_ready: 'Question bank not ready',
  engine_room_join_failed: "Couldn't reach interview room",
  engine_internal_error: 'Internal error',
  engine_unresponsive: 'Interview never started',
}

export function labelForErrorCode(code: string | null | undefined): string {
  if (!code) return 'Failed'
  return SESSION_ERROR_LABELS[code] ?? 'Failed'
}
