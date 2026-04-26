import { describe, expect, it } from 'vitest'

describe('Questions page — stage filter', () => {
  it('hides intake and debrief from the stage pill row', async () => {
    const { stageSupportsQuestionBank } = await import('@/lib/pipelines/categories')
    expect(stageSupportsQuestionBank('intake')).toBe(false)
    expect(stageSupportsQuestionBank('debrief')).toBe(false)
  })

  it('shows phone_screen, ai_screening, human_interview, take_home', async () => {
    const { stageSupportsQuestionBank } = await import('@/lib/pipelines/categories')
    expect(stageSupportsQuestionBank('phone_screen')).toBe(true)
    expect(stageSupportsQuestionBank('ai_screening')).toBe(true)
    expect(stageSupportsQuestionBank('human_interview')).toBe(true)
    expect(stageSupportsQuestionBank('take_home')).toBe(true)
  })
})
