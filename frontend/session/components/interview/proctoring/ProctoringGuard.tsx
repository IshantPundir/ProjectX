'use client'

import { useEffect, useState, type ReactNode } from 'react'
import { useVoiceAssistant, useSessionContext } from '@livekit/components-react'

import type { ProctoringConfig } from '@/lib/api/candidate-session'
import { useProctoringController } from './use-proctoring-controller'
import { useVisibilityGuard } from './use-visibility-guard'
import { useFocusGuard } from './use-focus-guard'
import { useKeyboardGuard } from './use-keyboard-guard'
import { useDevtoolsGuard } from './use-devtools-guard'
import { useFullscreenGuard } from './use-fullscreen-guard'
import { useVisionGuard } from './use-vision-guard'
import { ViolationBorder } from './ViolationBorder'
import { VisionDebugOverlay } from './VisionDebugOverlay'
import { FullscreenGraceOverlay } from './FullscreenGraceOverlay'
import { FocusGraceOverlay } from './FocusGraceOverlay'
import { ViolationNoticeOverlay } from './ViolationNoticeOverlay'
import type { ProctoringTermination } from './violation-kinds'
import { env } from '@/lib/env'

const ARM_SETTLE_MS = 800

const DISABLED: ProctoringConfig = {
  enabled: false,
  soft_violation_limit: 3,
  fullscreen_grace_seconds: 10,
}

export function ProctoringGuard({
  token,
  config,
  onTerminated,
  children,
}: {
  token: string
  config: ProctoringConfig | null
  onTerminated: (reason: ProctoringTermination) => void
  children: ReactNode
}) {
  const cfg = config ?? DISABLED
  const { state } = useVoiceAssistant()
  const ctx = useSessionContext() as unknown as { isConnected?: boolean }
  const connected = !!ctx?.isConnected
  const [visionSettled, setVisionSettled] = useState(false)

  // Two-tier arming. ENV guards (fullscreen/focus/visibility/keyboard/devtools)
  // arm as soon as the room is CONNECTED — they don't need the camera, so the
  // pre-conversation window is monitored identically to mid-interview (closes
  // the pre-start fullscreen-exit gap). The VISION guard waits an extra settle
  // after the agent goes live so the candidate getting seated doesn't
  // self-trigger a "looking away" nudge.
  useEffect(() => {
    if (visionSettled || !cfg.enabled) return
    if (state === 'listening' || state === 'thinking' || state === 'speaking') {
      const t = setTimeout(() => setVisionSettled(true), ARM_SETTLE_MS)
      return () => clearTimeout(t)
    }
  }, [state, visionSettled, cfg.enabled])

  const controller = useProctoringController({ token, config: cfg, onTerminated })
  const envArmed = cfg.enabled && connected
  const visionArmed = envArmed && visionSettled

  useVisibilityGuard({ armed: envArmed, onViolation: controller.report })
  useKeyboardGuard({ armed: envArmed, onViolation: controller.report })
  useDevtoolsGuard({ armed: envArmed, onViolation: controller.report })
  const focus = useFocusGuard({
    armed: envArmed,
    graceSeconds: cfg.fullscreen_grace_seconds,
    onViolation: controller.report,
  })
  const fs = useFullscreenGuard({
    armed: envArmed,
    graceSeconds: cfg.fullscreen_grace_seconds,
    onViolation: controller.report,
  })
  // Vision reports through the SAME controller (soft, counted). Armed only after
  // the agent-speech settle so seating movement doesn't self-trigger a nudge.
  const vision = useVisionGuard({ armed: visionArmed, onViolation: controller.report })

  return (
    <>
      {children}
      {cfg.enabled && <ViolationBorder flash={controller.flash} />}
      {cfg.enabled && controller.notice && (
        <ViolationNoticeOverlay
          key={controller.notice.key}
          kind={controller.notice.kind}
          softCount={controller.notice.softCount}
          limit={controller.notice.limit}
          onAcknowledge={controller.dismissNotice}
        />
      )}
      {cfg.enabled && fs.showOverlay && (
        <FullscreenGraceOverlay secondsLeft={fs.secondsLeft} onReturn={fs.returnToFullscreen} />
      )}
      {cfg.enabled && focus.showOverlay && <FocusGraceOverlay secondsLeft={focus.secondsLeft} />}
      {cfg.enabled && env.NEXT_PUBLIC_PROCTORING_DEBUG && (
        <VisionDebugOverlay signals={vision.signals} />
      )}
    </>
  )
}
