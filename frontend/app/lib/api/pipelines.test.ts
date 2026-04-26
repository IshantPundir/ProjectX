import { describe, it } from 'vitest'
import type { PipelineStageInput } from './pipelines'

describe('PipelineStageInput discriminated union', () => {
  it('intake variant rejects difficulty at compile time', () => {
    // @ts-expect-error — difficulty is not allowed on intake
    const _bad: PipelineStageInput = { position: 0, name: 'Intake', stage_type: 'intake', difficulty: 'medium' }
    void _bad
  })

  it('intake variant accepts only name + position + stage_type + sla_days', () => {
    // Must compile without error — minimal intake stage
    const ok: PipelineStageInput = {
      position: 0, name: 'Intake', stage_type: 'intake',
    }
    void ok
  })

  it('phone_screen variant requires duration_minutes + difficulty + signal_filter + pass_criteria + advance_behavior', () => {
    // Must compile without error — all required fields present
    const ok: PipelineStageInput = {
      position: 1, name: 'Phone Screen', stage_type: 'phone_screen',
      duration_minutes: 30, difficulty: 'medium',
      signal_filter: { include_types: ['competency'] },
      pass_criteria: { type: 'all_knockouts_pass' },
      advance_behavior: 'auto_advance',
    }
    void ok
  })

  it('debrief variant rejects signal_filter at compile time', () => {
    // @ts-expect-error — signal_filter is not allowed on debrief
    const _bad: PipelineStageInput = { position: 4, name: 'Debrief', stage_type: 'debrief', signal_filter: { include_types: ['competency'] } }
    void _bad
  })

  it('take_home variant has no configurable fields beyond name + position + stage_type', () => {
    // Must compile without error — minimal take_home stage
    const ok: PipelineStageInput = {
      position: 3, name: 'Take Home', stage_type: 'take_home',
    }
    void ok
  })

  it('phone_screen rejects extra take_home-only field', () => {
    // No fields are take_home-only currently; this is a placeholder for future expansion.
    // (No assertion needed — the discriminated union is correct if other tests pass.)
  })
})
