'use client'

import { useEffect, useRef } from 'react'
import type { GuardArgs } from './use-visibility-guard'

const NAV_KEYS = new Set(['Tab', 'Enter', ' ', 'Escape', 'Shift', 'Control', 'Alt', 'Meta'])
const KEYBOARD_DEBOUNCE_MS = 1500

function isDevtoolsCombo(e: KeyboardEvent): boolean {
  if (e.key === 'F12') return true
  return (e.ctrlKey || e.metaKey) && e.shiftKey && ['I', 'J', 'C'].includes(e.key.toUpperCase())
}

function isBlockedCombo(e: KeyboardEvent): boolean {
  return (e.ctrlKey || e.metaKey) && ['s', 'p', 'f'].includes(e.key.toLowerCase())
}

export function useKeyboardGuard({ armed, onViolation }: GuardArgs): void {
  const lastFired = useRef(0)
  useEffect(() => {
    if (!armed) return
    const onKey = (e: KeyboardEvent) => {
      // Block save/print/find + the devtools-open shortcuts (the open is also
      // caught hard by useDevtoolsGuard; the keypress is recorded as soft).
      if (isDevtoolsCombo(e) || isBlockedCombo(e)) e.preventDefault()
      if (NAV_KEYS.has(e.key)) return // keep the End button keyboard-operable
      const now = Date.now()
      if (now - lastFired.current < KEYBOARD_DEBOUNCE_MS) return
      lastFired.current = now
      onViolation('keyboard')
    }
    const onCtx = (e: MouseEvent) => e.preventDefault()
    window.addEventListener('keydown', onKey)
    window.addEventListener('contextmenu', onCtx)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('contextmenu', onCtx)
    }
  }, [armed, onViolation])
}
