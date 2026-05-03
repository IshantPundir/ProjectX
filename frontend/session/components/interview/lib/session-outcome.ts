/**
 * Shared SessionOutcome type — the single source of truth for the 6
 * outcome strings the engine publishes via the agent participant's
 * `session_outcome` attribute.
 *
 * Must stay in sync with backend `app/modules/interview_engine/outcome_close.py::SessionOutcome`.
 * If a value is added/removed here, update the backend list in the
 * same PR. The exhaustive switch in `OutcomeWatcher` (app/app.tsx)
 * uses `_exhaustive: never` to surface missed cases at compile time.
 */

export const SESSION_OUTCOMES = [
  'completed',
  'knockout_closed',
  'time_expired',
  'candidate_ended',
  'candidate_unresponsive',
  'error',
] as const

export type SessionOutcome = (typeof SESSION_OUTCOMES)[number]

/**
 * Runtime guard — drops unrecognized values to false. Defensive against
 * backend/frontend version skew (a future backend outcome the frontend
 * hasn't shipped support for yet should be ignored, not coerced).
 */
export function isSessionOutcome(v: unknown): v is SessionOutcome {
  return typeof v === 'string' && (SESSION_OUTCOMES as readonly string[]).includes(v)
}
