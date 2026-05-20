import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(join(__dirname, '../../app/globals.css'), 'utf8')

describe('cool-light theme', () => {
  it('declares the cool-light theme block', () => {
    expect(css).toContain('[data-px-theme="cool-light"]')
  })
  it('defines the prominent glass + app-base tokens', () => {
    for (const t of ['--px-glass-bg', '--px-glass-border', '--px-app-base', '--px-accent']) {
      expect(css).toContain(t)
    }
  })
  it('ships the aura-mark + animated-background styles with a reduced-motion guard', () => {
    expect(css).toContain('.aura-mark')
    expect(css).toContain('@keyframes px-drift')
    expect(css).toContain('prefers-reduced-motion: reduce')
  })
  it('no longer ships the retired dark-cinematic / liquid-aura styles', () => {
    expect(css).not.toContain('[data-px-theme="dark-cinematic"]')
    expect(css).not.toContain('liquid-aura')
  })
})
