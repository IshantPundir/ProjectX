import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AnimatedBackground } from '@/components/agents-ui/animated-background'

describe('AnimatedBackground', () => {
  it('renders a decorative, aria-hidden, fixed layer with drifting blobs', () => {
    const { container } = render(<AnimatedBackground />)
    const root = container.firstElementChild as HTMLElement
    expect(root).toHaveAttribute('aria-hidden', 'true')
    expect(root.className).toContain('fixed')
    expect(root.querySelectorAll('.px-bg-blob').length).toBeGreaterThanOrEqual(3)
  })
})
