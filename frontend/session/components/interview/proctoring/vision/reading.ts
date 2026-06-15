import type { GazeZone } from './types'

const WINDOW_MS = 5000 // rolling analysis window
const MIN_OFF_RATIO = 0.6 // fraction of window spent off-screen
const MIN_DIRECTION_CHANGES = 3 // left<->right alternations = scanning

const OFF_SCREEN: ReadonlySet<GazeZone> = new Set(['left', 'right', 'down_away', 'up'])

interface Sample {
  zone: GazeZone
  t: number
}

/**
 * Rolling-window reading detector. Flags sustained off-screen attention WITH a
 * horizontal scanning rhythm (the "reading a second screen" pattern). Driven by
 * head-pose-derived zones only (the caller passes pose-based zones) — never eye/
 * iris tracking. Consumed by use-vision-guard to strengthen looking_away_sustained.
 */
export class ReadingAccumulator {
  private samples: Sample[] = []

  /**
   * Append a sample and prune anything older than the rolling window.
   * Assumes `t` is monotonically non-decreasing (caller passes
   * performance.now() per frame); the prune pivot is the newest timestamp.
   */
  push(zone: GazeZone, t: number): void {
    this.samples.push({ zone, t })
    const cutoff = t - WINDOW_MS
    while (this.samples.length && this.samples[0].t < cutoff) this.samples.shift()
  }

  offScreenRatio(): number {
    if (this.samples.length === 0) return 0
    const off = this.samples.filter((s) => OFF_SCREEN.has(s.zone)).length
    return off / this.samples.length
  }

  private directionChanges(): number {
    let changes = 0
    let last: 'left' | 'right' | null = null
    for (const s of this.samples) {
      const dir = s.zone === 'left' ? 'left' : s.zone === 'right' ? 'right' : null
      if (dir && last && dir !== last) changes++
      if (dir) last = dir
    }
    return changes
  }

  isReading(): boolean {
    if (this.samples.length < 2) return false
    const span = this.samples[this.samples.length - 1].t - this.samples[0].t
    if (span < WINDOW_MS * 0.6) return false // need a sustained window
    return this.offScreenRatio() >= MIN_OFF_RATIO && this.directionChanges() >= MIN_DIRECTION_CHANGES
  }
}
