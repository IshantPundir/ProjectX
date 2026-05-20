'use client'

import { useEffect, useRef } from 'react'
import { useSessionContext } from '@livekit/components-react'

interface Props {
  onTimeout: () => void
  timeoutMs?: number
}

/**
 * Renders a dimming overlay while the LiveKit session is reconnecting.
 * Fires onTimeout after timeoutMs (default 30s) of continuous reconnect, so the
 * caller can route to DisconnectError with code RECONNECT_FAILED.
 *
 * `connectionState` here is livekit-client's ConnectionState enum, exposed
 * through useSessionContext's return value. Both 'reconnecting' and
 * 'signalReconnecting' indicate an in-flight reconnect.
 */
export function ReconnectingOverlay({ onTimeout, timeoutMs = 30_000 }: Props) {
  const ctx = useSessionContext() as unknown as { connectionState?: string }
  const isReconnecting =
    ctx?.connectionState === 'reconnecting' ||
    ctx?.connectionState === 'signalReconnecting'
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
    <div role="alert" className="fixed inset-0 z-50 grid place-items-center bg-black/55 backdrop-blur-sm">
      <div className="px-glass-strong rounded-2xl px-8 py-7 text-center">
        <div className="mx-auto mb-3 size-8 animate-spin rounded-full border-4 border-px-hairline border-t-px-accent-soft motion-reduce:animate-none" />
        <p className="text-sm font-medium text-px-fg">Reconnecting…</p>
        <p className="mt-1 text-xs text-px-fg-4">Please don&apos;t close this tab.</p>
      </div>
    </div>
  )
}
