'use client'

import { useEffect, useState, type ReactNode } from 'react'
import { useVoiceAssistant } from '@livekit/components-react'

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
  const [armed, setArmed] = useState(false)

  // Arm only once the agent is live + a short settle window, so the LiveKit
  // connect, media publish, and the start-gesture fullscreen entry all settle
  // before enforcement begins (prevents self-inflicted terminations).
  useEffect(() => {
    if (armed || !cfg.enabled) return
    if (state === 'listening' || state === 'thinking' || state === 'speaking') {
      const t = setTimeout(() => setArmed(true), ARM_SETTLE_MS)
      return () => clearTimeout(t)
    }
  }, [state, armed, cfg.enabled])

  const controller = useProctoringController({ token, config: cfg, onTerminated })
  const enforce = armed && cfg.enabled

  useVisibilityGuard({ armed: enforce, onViolation: controller.report })
  useKeyboardGuard({ armed: enforce, onViolation: controller.report })
  useDevtoolsGuard({ armed: enforce, onViolation: controller.report })
  const focus = useFocusGuard({
    armed: enforce,
    graceSeconds: cfg.fullscreen_grace_seconds,
    onViolation: controller.report,
  })
  const fs = useFullscreenGuard({
    armed: enforce,
    graceSeconds: cfg.fullscreen_grace_seconds,
    onViolation: controller.report,
  })
  // Vision violations report through the SAME controller as the behavioral
  // guards: soft, counted toward the shared limit, backend-terminated on
  // escalation (same toast + border flash as tab-switch etc.).
  const vision = useVisionGuard({ armed: enforce, onViolation: controller.report })

  return (
    <>
      {children}
      {cfg.enabled && <ViolationBorder flash={controller.flash} />}
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
