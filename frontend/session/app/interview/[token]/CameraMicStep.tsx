'use client'

import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/px'
import { sampleNoiseFloorDbfs } from './sampleNoiseFloorDbfs'

interface Props {
  onPass: () => void
}

type Status = 'idle' | 'prompting' | 'sampling' | 'ready' | 'denied'

// Threshold for "noisy" environment, in dBFS (decibels relative to full
// scale). Phase 6 disables browser-side EC/NS/AGC, so the dBFS reading
// now reflects RAW ambient audio (was post-EC/NS/AGC pre-Phase-6). Same
// physical room reads ~10 dBFS higher (closer to 0). Threshold pushed
// up by ~10 dBFS to match: post-Phase-6 quiet rooms read ~-35 to -40,
// office ambient -25 to -20, coffee shops -20 to -10. We warn (not
// block) above -20. Tune later if real-world readings drift.
const NOISE_WARN_DBFS = -20

export function CameraMicStep({ onPass }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [status, setStatus] = useState<Status>('idle')
  const [error, setError] = useState<string | null>(null)
  const [noiseDbfs, setNoiseDbfs] = useState<number | null>(null)

  const start = async () => {
    setStatus('prompting')
    setError(null)
    setNoiseDbfs(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: true,
        // Phase 6: disable browser-side EC/NS/AGC so ai_coustics
        // becomes the single noise filter in the audio path. See
        // docs/security/threat-model.md Phase 6 section.
        //
        // Plain `false` (ideal constraint) is intentional. Hard
        // rejection via `{ exact: false }` would break cam/mic on
        // browsers that silently ignore these flags (notably mobile
        // Safari). The track.getSettings() check below detects the
        // silent-ignore case and structured-logs it — but the session
        // continues regardless per the Phase 6 browser-divergence
        // decision.
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      })
      // Phase 6: detect browsers that silently ignore the constraint
      // object (notably mobile Safari on iOS, sometimes mobile Chrome
      // on Android). Log-only — no candidate-facing warning, since the
      // candidate has no actionable knob. Operators monitor the log.
      // The session continues regardless. See docs/security/threat-model.md
      // Phase 6 "Browser-divergence decision" for the residual-risk
      // analysis.
      const audioTrack = stream.getAudioTracks()[0]
      if (audioTrack) {
        const applied = audioTrack.getSettings()
        const diverged =
          applied.echoCancellation !== false ||
          applied.noiseSuppression !== false ||
          applied.autoGainControl !== false
        if (diverged) {
          console.warn('cammic.constraints.diverged', {
            requested: {
              echoCancellation: false,
              noiseSuppression: false,
              autoGainControl: false,
            },
            applied: {
              echoCancellation: applied.echoCancellation,
              noiseSuppression: applied.noiseSuppression,
              autoGainControl: applied.autoGainControl,
            },
          })
        }
      }
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
      }
      setStatus('sampling')
      // Sample is best-effort: failure here must not block Continue.
      const dbfs = await sampleNoiseFloorDbfs(stream)
      setNoiseDbfs(dbfs)
      setStatus('ready')
    } catch (err) {
      const name = (err as Error).name
      if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
        setError(
          'Permission denied. Please enable camera and microphone in your browser settings.',
        )
      } else if (name === 'NotFoundError') {
        setError('No camera or microphone detected on this device.')
      } else {
        setError((err as Error).message)
      }
      setStatus('denied')
    }
  }

  useEffect(() => {
    return () => {
      streamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  const noisy = noiseDbfs !== null && noiseDbfs > NOISE_WARN_DBFS

  return (
    <section className="space-y-6">
      <div
        className="rounded-[12px] border p-6"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <div
          className="mb-2 text-[10.5px] font-semibold uppercase"
          style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
        >
          Camera & microphone
        </div>
        <h2
          className="px-serif m-0 mb-2 text-[24px] font-normal"
          style={{ letterSpacing: '-0.4px', color: 'var(--px-fg)' }}
        >
          Let&apos;s check your setup
        </h2>
        <p
          className="mb-2 text-[14px]"
          style={{ color: 'var(--px-fg-2)', lineHeight: 1.6 }}
        >
          We&apos;ll access your camera and microphone during the interview.
          Test them now:
        </p>
        <p
          className="mb-4 text-[12.5px]"
          style={{ color: 'var(--px-fg-3)', lineHeight: 1.6 }}
        >
          Tip: For the cleanest call, headphones are recommended.
        </p>
        <div
          className="aspect-video w-full overflow-hidden rounded-[10px]"
          style={{ background: '#16140F' }}
        >
          <video
            ref={videoRef}
            autoPlay
            muted
            playsInline
            className="h-full w-full object-cover"
          />
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {status === 'idle' && (
            <Button onClick={start}>Test camera &amp; mic</Button>
          )}
          {status === 'prompting' && (
            <p className="text-sm" style={{ color: 'var(--px-fg-3)' }}>
              Waiting for permission…
            </p>
          )}
          {status === 'sampling' && (
            <p className="text-sm" style={{ color: 'var(--px-fg-3)' }}>
              Listening for background noise (stay quiet for a moment)…
            </p>
          )}
          {status === 'ready' && (
            <>
              <span
                className="text-sm font-medium"
                style={{ color: 'var(--px-ok)' }}
              >
                Camera and mic are working ✓
              </span>
              <Button onClick={onPass}>Continue →</Button>
            </>
          )}
          {status === 'denied' && (
            <>
              <span className="text-sm" style={{ color: 'var(--px-danger)' }}>
                {error}
              </span>
              <Button variant="outline" onClick={start}>
                Retry
              </Button>
            </>
          )}
        </div>
        {status === 'ready' && noisy && (
          <p
            className="mt-3 text-[13px] text-amber-700"
            style={{ lineHeight: 1.6 }}
            role="status"
          >
            Your environment sounds noisy. This measures your raw room
            noise — our audio processing handles a fair bit on top, so the
            interview will still work. For the cleanest call, find a
            quieter spot.
          </p>
        )}
      </div>
    </section>
  )
}
