'use client'

import { useCallback, useRef, useState } from 'react'
import { useSessionContext } from '@livekit/components-react'
import { toast } from 'sonner'

import {
  candidateSessionApi,
  type ProctoringConfig,
  type ProctoringKind,
} from '@/lib/api/candidate-session'
import { isHard, VIOLATION_LABEL, type ProctoringTermination } from './violation-kinds'

export interface BorderFlash {
  tone: 'hard' | 'soft'
  key: number
}

export interface ViolationNotice {
  kind: ProctoringKind
  softCount: number
  limit: number
  key: number
}

export interface UseProctoringControllerArgs {
  token: string
  config: ProctoringConfig
  onTerminated: (reason: ProctoringTermination) => void
}

export interface ProctoringController {
  report: (kind: ProctoringKind) => Promise<void>
  flash: BorderFlash | null
  notice: ViolationNotice | null
  dismissNotice: () => void
}

export function useProctoringController({
  token,
  config,
  onTerminated,
}: UseProctoringControllerArgs): ProctoringController {
  const ctx = useSessionContext() as unknown as { end?: () => void }
  const [flash, setFlash] = useState<BorderFlash | null>(null)
  const flashKey = useRef(0)
  const softCount = useRef(0)
  const [notice, setNotice] = useState<ViolationNotice | null>(null)
  const noticeKey = useRef(0)
  const dismissNotice = useCallback(() => setNotice(null), [])
  const terminatedRef = useRef(false)

  const terminate = useCallback(
    (reason: ProctoringTermination) => {
      if (terminatedRef.current) return
      terminatedRef.current = true
      onTerminated(reason) // sets the app-level terminal ref synchronously
      ctx.end?.() // disconnect; OutcomeWatcher is guarded against this
    },
    [ctx, onTerminated],
  )

  const report = useCallback(
    async (kind: ProctoringKind) => {
      if (terminatedRef.current) return

      const hard = isHard(kind)
      flashKey.current += 1
      setFlash({ tone: hard ? 'hard' : 'soft', key: flashKey.current })

      if (hard) {
        // Fail-safe: record best-effort, end locally regardless of the POST.
        void candidateSessionApi
          .proctoringEvent(token, { kind, occurred_at: new Date().toISOString() })
          .catch(() => {})
        toast.error(`Interview ending — ${VIOLATION_LABEL[kind]} is not permitted.`)
        terminate(kind)
        return
      }

      // Soft: surface a modal notice, then let the backend decide the threshold.
      softCount.current += 1
      noticeKey.current += 1
      setNotice({
        kind,
        softCount: softCount.current,
        limit: config.soft_violation_limit,
        key: noticeKey.current,
      })
      try {
        const res = await candidateSessionApi.proctoringEvent(token, {
          kind,
          occurred_at: new Date().toISOString(),
        })
        if (res.terminated) terminate('soft_threshold_exceeded')
      } catch {
        // Network failure on a soft violation: keep the interview running.
      }
    },
    [token, config.soft_violation_limit, terminate],
  )

  return { report, flash, notice, dismissNotice }
}
