'use client'

import { useEffect, useRef } from 'react'
import { useSessionContext } from '@livekit/components-react'

interface Props {
  onTimeout: () => void
  timeoutMs?: number
}

/**
 * Renders a dimming overlay while the LiveKit session is in 'reconnecting' state.
 * Fires onTimeout after timeoutMs (default 30s) of continuous reconnect, so the
 * caller can route to DisconnectError with code RECONNECT_FAILED.
 *
 * Outcome state is owned by the parent <App>; this component is purely UI + a
 * timer. The state shape returned by useSessionContext is verified at runtime —
 * we read the literal string 'reconnecting' loosely.
 */
export function ReconnectingOverlay({ onTimeout, timeoutMs = 30_000 }: Props) {
  const ctx = useSessionContext() as unknown as { state?: string }
  const isReconnecting = ctx?.state === 'reconnecting'
  const firedRef = useRef(false)

  useEffect(() => {
    if (!isReconnecting) {
      firedRef.current = false
      return
    }
    const t = setTimeout(() => {
      if (firedRef.current) return
      firedRef.current = true
      onTimeout()
    }, timeoutMs)
    return () => clearTimeout(t)
  }, [isReconnecting, timeoutMs, onTimeout])

  if (!isReconnecting) return null

  return (
    <div
      role="alert"
      className="fixed inset-0 z-50 grid place-items-center bg-black/40 backdrop-blur-sm"
    >
      <div className="rounded-xl bg-white p-8 text-center shadow-lg">
        <div className="mx-auto mb-3 size-8 animate-spin rounded-full border-4 border-zinc-300 border-t-zinc-900" />
        <p className="text-sm font-medium text-zinc-900">Reconnecting…</p>
        <p className="mt-1 text-xs text-zinc-500">Please don&apos;t close this tab.</p>
      </div>
    </div>
  )
}
