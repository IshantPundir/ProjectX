import { describe, expect, it } from 'vitest'
import type { StageType, StageParticipantResponse } from '@/lib/api/pipelines'
import {
  stageCategory,
  participantSlotsFor,
  isStageUnstaffed,
} from './categories'

describe('stageCategory', () => {
  const cases: Array<[StageType, ReturnType<typeof stageCategory>]> = [
    ['intake', 'entry'],
    ['phone_screen', 'human_led'],
    ['human_interview', 'human_led'],
    ['ai_screening', 'ai_led'],
    ['debrief', 'review'],
    ['take_home', 'disabled'],
  ]
  it.each(cases)('maps %s to %s', (type, expected) => {
    expect(stageCategory(type)).toBe(expected)
  })
})

describe('participantSlotsFor', () => {
  it('returns [] for entry', () => {
    expect(participantSlotsFor('intake')).toEqual([])
  })
  it('returns interviewer slot for human_led', () => {
    expect(participantSlotsFor('human_interview')).toEqual([
      { role: 'interviewer', required: true, min: 1 },
    ])
    expect(participantSlotsFor('phone_screen')).toEqual([
      { role: 'interviewer', required: true, min: 1 },
    ])
  })
  it('returns optional observer slot for ai_led', () => {
    expect(participantSlotsFor('ai_screening')).toEqual([
      { role: 'observer', required: false },
    ])
  })
  it('returns reviewer slot for review', () => {
    expect(participantSlotsFor('debrief')).toEqual([
      { role: 'reviewer', required: true, min: 1 },
    ])
  })
  it('returns [] for disabled take_home', () => {
    expect(participantSlotsFor('take_home')).toEqual([])
  })
})

describe('isStageUnstaffed', () => {
  const p = (role: 'interviewer' | 'observer' | 'reviewer'): StageParticipantResponse => ({
    user_id: 'u',
    role,
    full_name: 'U',
    email: 'u@example.com',
  })
  it('true when human_interview has 0 interviewers', () => {
    expect(isStageUnstaffed({ stage_type: 'human_interview', participants: [] })).toBe(true)
  })
  it('false when human_interview has at least 1 interviewer', () => {
    expect(
      isStageUnstaffed({ stage_type: 'human_interview', participants: [p('interviewer')] }),
    ).toBe(false)
  })
  it('false for ai_screening (observers optional)', () => {
    expect(isStageUnstaffed({ stage_type: 'ai_screening', participants: [] })).toBe(false)
  })
  it('false for intake / take_home', () => {
    expect(isStageUnstaffed({ stage_type: 'intake', participants: [] })).toBe(false)
    expect(isStageUnstaffed({ stage_type: 'take_home', participants: [] })).toBe(false)
  })
  it('true when debrief has 0 reviewers', () => {
    expect(isStageUnstaffed({ stage_type: 'debrief', participants: [] })).toBe(true)
  })
})
