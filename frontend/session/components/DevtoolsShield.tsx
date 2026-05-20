'use client'

import { useEffect, useState } from 'react'

/**
 * Site-wide developer-tools deterrent. Mounted once in the root layout so it
 * applies to EVERY candidate page — landing, the pre-join wizard, and the live
 * session.
 *
 * Two layers:
 *   1. Shortcut + right-click blocking (always on, prod-reliable, no false
 *      positives): F12, Ctrl/Cmd+Shift+I/J/C, Ctrl/Cmd+U (view-source), and
 *      the context menu are preventDefault-ed. This is the load-bearing
 *      deterrent — it stops the reflexive ways a candidate opens devtools.
 *   2. Open-detection → blocking overlay (best-effort): a `debugger`-statement
 *      timing trap. When devtools is open the statement pauses execution, so a
 *      large time delta means devtools is open → cover the page until closed.
 *
 * Honest limitation: Next's production minifier strips `debugger` statements
 * and the candidate-session CSP forbids `eval`/`new Function`, so the trap is
 * effective in development but a no-op in production. We deliberately do NOT
 * use the window-size heuristic here because it false-positives on browser
 * zoom and would lock a legitimate (e.g. accessibility-zoom) candidate out of
 * the whole site. The live interview separately ends the session on devtools
 * via its own size-delta detector (acceptable there: one-shot, mid-interview).
 */

const POLL_MS = 1000
const PAUSE_THRESHOLD_MS = 120

function isDevtoolsShortcut(e: KeyboardEvent): boolean {
  if (e.key === 'F12') return true
  const mod = e.ctrlKey || e.metaKey
  // Windows/Linux use Shift; macOS uses Option (Alt) — cover both.
  const second = e.shiftKey || e.altKey
  const key = e.key.toUpperCase()
  if (mod && second && (key === 'I' || key === 'J' || key === 'C')) return true
  if (mod && key === 'U') return true // view-source
  return false
}

export function DevtoolsBlockedOverlay() {
  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-label="Developer tools detected"
      className="fixed inset-0 z-[100] grid place-items-center bg-black/80 p-6 text-center backdrop-blur-xl"
    >
      <div className="px-glass-strong max-w-md rounded-2xl px-8 py-10">
        <h2 className="font-serif text-2xl text-px-fg">Developer tools detected</h2>
        <p className="mt-3 text-sm text-px-fg-3">
          Developer tools must be closed to use this interview. Please close them to continue.
        </p>
      </div>
    </div>
  )
}

export function DevtoolsShield() {
  const [open, setOpen] = useState(false)

  // Layer 1 — block the open shortcuts + right-click everywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isDevtoolsShortcut(e)) e.preventDefault()
    }
    const onContextMenu = (e: MouseEvent) => e.preventDefault()
    window.addEventListener('keydown', onKey)
    window.addEventListener('contextmenu', onContextMenu)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('contextmenu', onContextMenu)
    }
  }, [])

  // Layer 2 — continuous open-detection via the debugger timing trap.
  useEffect(() => {
    let cancelled = false
    const id = window.setInterval(() => {
      const t0 = performance.now()
      // eslint-disable-next-line no-debugger
      debugger
      if (cancelled) return
      // The statement only pauses when devtools is open; a large delta toggles
      // the overlay on, a near-zero delta (devtools closed) toggles it back off.
      setOpen(performance.now() - t0 > PAUSE_THRESHOLD_MS)
    }, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [])

  return open ? <DevtoolsBlockedOverlay /> : null
}
