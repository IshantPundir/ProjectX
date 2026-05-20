import { describe, expect, it } from 'vitest'
import { formatClock, questionLabel } from '@/components/interview/session/format-progress'

describe('formatClock', () => {
  it('formats seconds as M:SS', () => {
    expect(formatClock(0)).toBe('0:00')
    expect(formatClock(9)).toBe('0:09')
    expect(formatClock(75)).toBe('1:15')
    expect(formatClock(600)).toBe('10:00')
  })
  it('never returns negative time', () => {
    expect(formatClock(-5)).toBe('0:00')
  })
})

describe('questionLabel', () => {
  it('renders 1-based and clamps to total', () => {
    expect(questionLabel(0, 8)).toBe('Question 1 of 8')
    expect(questionLabel(7, 8)).toBe('Question 8 of 8')
    expect(questionLabel(9, 8)).toBe('Question 8 of 8')
  })
})
