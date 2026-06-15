import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HeroScene } from '@/app/interview/[token]/illustrations/HeroScene'
import {
  ArjunGlyph,
  QuietRoomGlyph,
  SingleScreenGlyph,
  ShieldGlyph,
  OneTimeLinkGlyph,
} from '@/app/interview/[token]/illustrations/glyphs'

describe('illustrations', () => {
  it('renders the hero scene as a decorative svg', () => {
    const { container } = render(<HeroScene />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    expect(svg).toHaveAttribute('aria-hidden', 'true')
  })

  it.each([
    ['arjun', ArjunGlyph],
    ['quiet', QuietRoomGlyph],
    ['screen', SingleScreenGlyph],
    ['shield', ShieldGlyph],
    ['link', OneTimeLinkGlyph],
  ])('renders the %s glyph svg', (_name, Glyph) => {
    const { container } = render(<Glyph />)
    expect(container.querySelector('svg')).not.toBeNull()
  })
})
