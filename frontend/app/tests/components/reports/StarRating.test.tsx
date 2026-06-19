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

test('clipPath and linearGradient ids are unique across multiple instances', () => {
  const { container } = render(
    <>
      <StarRating valueTen={9} />
      <StarRating valueTen={3} />
    </>
  )
  const clipPaths = container.querySelectorAll('clipPath')
  const clipIds = Array.from(clipPaths).map((el) => el.getAttribute('id') ?? '')
  expect(new Set(clipIds).size).toBe(clipIds.length)

  const gradients = container.querySelectorAll('linearGradient')
  const gradientIds = Array.from(gradients).map((el) => el.getAttribute('id') ?? '')
  expect(new Set(gradientIds).size).toBe(gradientIds.length)
})
