import type { StageType, StageParticipantResponse } from '@/lib/api/pipelines'

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

export function isStageUnstaffed(stage: {
  stage_type: StageType
  participants: StageParticipantResponse[]
}): boolean {
  const required = participantSlotsFor(stage.stage_type).filter(
    (s): s is Extract<ParticipantSlotSpec, { required: true }> => s.required,
  )
  return required.some(
    (slot) => stage.participants.filter((p) => p.role === slot.role).length === 0,
  )
}
