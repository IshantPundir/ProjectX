'use client'

/**
 * Multi-display detection via the permission-free Window Management API
 * (`window.screen.isExtended`). Returns `null` when unsupported (Firefox/Safari) —
 * callers MUST treat null as "cannot determine" and NOT block. We deliberately
 * avoid `getScreenDetails()` (it triggers a permission prompt). See spec §8.
 */
export function isMultiDisplay(): boolean | null {
  if (typeof window === 'undefined' || !window.screen) return null
  const ext = (window.screen as Screen & { isExtended?: boolean }).isExtended
  return typeof ext === 'boolean' ? ext : null
}

type ScreenWithEvents = Screen & {
  addEventListener?: (type: 'change', cb: () => void) => void
  removeEventListener?: (type: 'change', cb: () => void) => void
}

/**
 * Subscribe to display-topology changes (a monitor plugged/unplugged). Returns
 * an unsubscribe fn. No-op (returns a noop) when the API is unavailable.
 */
export function subscribeDisplayChange(onChange: () => void): () => void {
  if (typeof window === 'undefined' || !window.screen) return () => {}
  const screen = window.screen as ScreenWithEvents
  if (!screen.addEventListener) return () => {}
  screen.addEventListener('change', onChange)
  return () => screen.removeEventListener?.('change', onChange)
}
