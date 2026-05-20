import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(join(__dirname, '../../app/globals.css'), 'utf8')

describe('dark-cinematic theme', () => {
  it('declares the dark-cinematic theme block', () => {
    expect(css).toContain('[data-px-theme="dark-cinematic"]')
  })

  it('defines the glass + accent-bright tokens used by the cinematic UI', () => {
    for (const token of ['--px-glass-bg', '--px-glass-border', '--px-accent-bright', '--px-cine-backdrop']) {
      expect(css).toContain(token)
    }
  })

  it('ships the liquid-aura styles and a reduced-motion guard', () => {
    expect(css).toContain('.liquid-aura')
    expect(css).toContain('@keyframes liquid-aura-morph')
    expect(css).toContain('prefers-reduced-motion: reduce')
  })
})
