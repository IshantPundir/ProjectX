import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { BrandMark } from '@/components/interview/BrandMark'

describe('BrandMark', () => {
  it('renders the BinQle mark with accessible alt text', () => {
    render(<BrandMark variant="mark" />)
    const img = screen.getByRole('img', { name: /binqle\.ai/i })
    expect(img).toHaveAttribute('src', expect.stringContaining('binqle-mark'))
  })

  it('renders the wordmark variant', () => {
    render(<BrandMark variant="wordmark" />)
    const img = screen.getByRole('img', { name: /binqle\.ai/i })
    expect(img).toHaveAttribute('src', expect.stringContaining('binqle-wordmark'))
  })
})
