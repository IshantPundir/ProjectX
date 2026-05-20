/** Format a non-negative seconds count as M:SS. */
export function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  const m = Math.floor(s / 60)
  const sec = s % 60
  return `${m}:${String(sec).padStart(2, '0')}`
}

/** "Question X of N", 1-based, clamped so it never exceeds the total. */
export function questionLabel(zeroBasedIndex: number, total: number): string {
  const display = Math.min(zeroBasedIndex + 1, total)
  return `Question ${display} of ${total}`
}
