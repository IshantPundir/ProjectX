import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import {
  ArjunGlyph,
  QuietRoomGlyph,
  SingleScreenGlyph,
  ShieldGlyph,
  OneTimeLinkGlyph,
} from '@/app/interview/[token]/illustrations/glyphs'

describe('instruction glyphs', () => {
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
