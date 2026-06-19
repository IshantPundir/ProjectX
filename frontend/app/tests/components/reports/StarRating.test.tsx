// tests/components/reports/StarRating.test.tsx
import { render, screen } from '@testing-library/react'
import { StarRating } from '@/components/dashboard/reports/StarRating'

test('renders an accessible 0-5 label from a 0-10 value', () => {
  render(<StarRating valueTen={9} />)
  expect(screen.getByLabelText('4.5 out of 5')).toBeInTheDocument()
})

test('clamps and labels a full score', () => {
  render(<StarRating valueTen={10} />)
  expect(screen.getByLabelText('5 out of 5')).toBeInTheDocument()
})
