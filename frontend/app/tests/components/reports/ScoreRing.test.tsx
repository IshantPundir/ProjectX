import { expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScoreRing } from '@/components/dashboard/reports/ScoreRing'

test('shows the 0-10 value and label', () => {
  render(<ScoreRing valueTen={8.1} label="Overall" />)
  expect(screen.getByText('8.1')).toBeInTheDocument()
  expect(screen.getByText('Overall')).toBeInTheDocument()
})

test('renders an em-dash when not assessed', () => {
  render(<ScoreRing valueTen={null} label="Behavioral" />)
  expect(screen.getByText('—')).toBeInTheDocument()
})
