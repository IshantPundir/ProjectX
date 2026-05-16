import type { JobPipelineInstance, ActivationPredicateFailure } from '@/lib/api/pipelines'

// Minimal bank info needed for the activation check.
type BankSummary = { stage_id: string; status: string }

const MIDDLE_TYPES = new Set(['phone_screen', 'ai_screening', 'human_interview'])
const HUMAN_LED_TYPES = new Set(['phone_screen', 'human_interview'])
const BANK_ELIGIBLE_TYPES = new Set(['phone_screen', 'ai_screening', 'human_interview'])

export function computeActivationFailures(
  pipeline: JobPipelineInstance,
  banks: BankSummary[],
): ActivationPredicateFailure[] {
  const failures: ActivationPredicateFailure[] = []

  const stages = pipeline.stages ?? []
  const middleStages = stages.filter((s) => MIDDLE_TYPES.has(s.stage_type))
  if (middleStages.length === 0) {
    failures.push({
      code: 'no_middle_stage',
      message: 'Add at least one screening stage between Intake and Debrief.',
      stage_id: null,
    })
  }

  const banksByStage: Record<string, BankSummary> = {}
  for (const b of banks) banksByStage[b.stage_id] = b

  for (const s of stages) {
    if (!s.name?.trim()) {
      failures.push({
        code: 'empty_stage_name',
        message: `Stage at position ${s.position} has no name.`,
        stage_id: s.id,
      })
    }
    if (HUMAN_LED_TYPES.has(s.stage_type)) {
      const interviewers = (s.participants ?? []).filter((p) => p.role === 'interviewer')
      if (interviewers.length === 0) {
        failures.push({
          code: 'missing_interviewer',
          message: `Assign an interviewer to '${s.name}'.`,
          stage_id: s.id,
        })
      }
    }
    if (s.stage_type === 'debrief') {
      const reviewers = (s.participants ?? []).filter((p) => p.role === 'reviewer')
      if (reviewers.length === 0) {
        failures.push({
          code: 'missing_reviewer',
          message: `Assign a reviewer to '${s.name}'.`,
          stage_id: s.id,
        })
      }
    }
    if (BANK_ELIGIBLE_TYPES.has(s.stage_type)) {
      const bank = banksByStage[s.id]
      // 'reviewing' is the post-generation pre-approval state — the recruiter
      // hasn't clicked "Confirm bank" yet, so the bank is not ready. Only
      // 'confirmed' opens the activation gate. Same failure code in both
      // shapes so the banner's in-flight-generation suppression check still
      // works; the message differs so the recruiter knows what to do next.
      if (!bank) {
        failures.push({
          code: 'missing_bank',
          message: `Generate a question bank for '${s.name}'.`,
          stage_id: s.id,
        })
      } else if (bank.status !== 'confirmed') {
        failures.push({
          code: 'missing_bank',
          message: `Confirm the question bank for '${s.name}'.`,
          stage_id: s.id,
        })
      }
    }
  }

  return failures
}
