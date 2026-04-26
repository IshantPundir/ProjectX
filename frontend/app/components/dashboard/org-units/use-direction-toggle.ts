import { useEffect, useState } from 'react'

const KEY = 'org-graph-direction'

export type Direction = 'TB' | 'LR'

function readPersistedDirection(): Direction {
  if (typeof window === 'undefined') return 'TB'
  try {
    const v = window.localStorage.getItem(KEY)
    return v === 'TB' || v === 'LR' ? v : 'TB'
  } catch {
    return 'TB'
  }
}

/**
 * `localStorage`-synced state for the org-graph layout direction.
 * SSR-safe: returns 'TB' on the server; the client hydrates with the
 * persisted value (no flash because the canvas is client-only).
 */
export function useDirectionToggle(): readonly [
  Direction,
  (d: Direction) => void,
] {
  const [direction, setDirection] = useState<Direction>(readPersistedDirection)

  useEffect(() => {
    try {
      window.localStorage.setItem(KEY, direction)
    } catch {
      // quota exceeded or private mode — keep in-memory state, do not crash.
    }
  }, [direction])

  return [direction, setDirection] as const
}
