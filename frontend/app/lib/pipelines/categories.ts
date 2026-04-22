import type { ParticipantRole, StageType } from '@/lib/api/pipelines'

export type StageCategory = 'entry' | 'human_led' | 'ai_led' | 'review' | 'disabled'

export function stageCategory(type: StageType): StageCategory {
  switch (type) {
    case 'intake':
      return 'entry'
    case 'phone_screen':
    case 'human_interview':
      return 'human_led'
    case 'ai_screening':
      return 'ai_led'
    case 'debrief':
      return 'review'
    case 'take_home':
      return 'disabled'
  }
}

export type ParticipantSlotSpec =
  | { role: 'interviewer'; required: true; min: 1 }
  | { role: 'observer'; required: false }
  | { role: 'reviewer'; required: true; min: 1 }

export function participantSlotsFor(type: StageType): ParticipantSlotSpec[] {
  switch (stageCategory(type)) {
    case 'human_led':
      return [{ role: 'interviewer', required: true, min: 1 }]
    case 'ai_led':
      return [{ role: 'observer', required: false }]
    case 'review':
      return [{ role: 'reviewer', required: true, min: 1 }]
    default:
      return []
  }
}

// Only reads `.role` on each participant — accepts any shape carrying that
// (StageParticipantInput, StageParticipantResponse, or a minimal pick).
export function isStageUnstaffed(stage: {
  stage_type: StageType
  participants?: readonly { role: ParticipantRole }[] | null
}): boolean {
  const required = participantSlotsFor(stage.stage_type).filter(
    (s): s is Extract<ParticipantSlotSpec, { required: true }> => s.required,
  )
  const participants = stage.participants ?? []
  return required.some(
    (slot) => participants.filter((p) => p.role === slot.role).length === 0,
  )
}
