import { useCallback, useSyncExternalStore } from 'react'

const KEY = 'org-graph-direction'
const STORAGE_EVENT = 'storage'

export type Direction = 'TB' | 'LR'

function parseDirection(raw: string | null | undefined): Direction {
  return raw === 'LR' ? 'LR' : 'TB'
}

// In-memory fallback for environments where `localStorage.setItem`
// throws (private mode, quota exceeded). After a failed write we keep
// serving this value so the user's choice is honoured for the rest of
// the session — without it, `setDirection('LR')` would be silently
// ignored on the next read.
let memoryFallback: Direction | null = null

function getClientSnapshot(): Direction {
  if (memoryFallback !== null) return memoryFallback
  try {
    return parseDirection(window.localStorage.getItem(KEY))
  } catch {
    return 'TB'
  }
}

// SSR snapshot: there is no localStorage on the server, and the
// client's hydration render must produce identical HTML, so both sides
// start at 'TB'. The store immediately re-reads on the client after
// hydration, flipping to the persisted value with no DOM mismatch
// because useSyncExternalStore subscribes to changes via tearing-safe
// commit-phase reads. See React docs on useSyncExternalStore + SSR.
function getServerSnapshot(): Direction {
  return 'TB'
}

function subscribe(notify: () => void): () => void {
  // Cross-tab updates: native `storage` events fire when another tab
  // writes the key. Same-tab writes go through `setDirection` below
  // and dispatch a synthetic event so subscribers re-read.
  function onStorage(event: StorageEvent) {
    if (event.key === KEY || event.key === null) notify()
  }
  window.addEventListener(STORAGE_EVENT, onStorage)
  return () => window.removeEventListener(STORAGE_EVENT, onStorage)
}

/**
 * `localStorage`-synced state for the org-graph layout direction.
 *
 * Uses `useSyncExternalStore` so SSR + hydration produce identical
 * HTML ('TB' on both server and client's first paint), and the
 * persisted value (often 'LR') is picked up immediately after commit.
 * This avoids the hydration mismatch that bit us with the older
 * `useState(readLocalStorage)` initializer in React 19 / Next 16.
 */
export function useDirectionToggle(): readonly [
  Direction,
  (d: Direction) => void,
] {
  const direction = useSyncExternalStore(
    subscribe,
    getClientSnapshot,
    getServerSnapshot,
  )

  const setDirection = useCallback((next: Direction) => {
    // Always honour the choice in-memory first so private-mode users
    // (where setItem throws) still see their selection take effect.
    memoryFallback = next
    try {
      window.localStorage.setItem(KEY, next)
    } catch {
      // quota exceeded or private mode — memoryFallback already covers it.
    }
    // Same-tab notification: `storage` does not fire in the originating
    // tab. Dispatch a synthetic event with the right key so our
    // subscriber re-reads. We hand-build a `StorageEvent`-shaped
    // CustomEvent because `new StorageEvent(...)` is read-only.
    window.dispatchEvent(
      Object.assign(new Event(STORAGE_EVENT), {
        key: KEY,
        newValue: next,
      }),
    )
  }, [])

  return [direction, setDirection] as const
}
