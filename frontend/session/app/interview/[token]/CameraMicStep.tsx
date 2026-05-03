'use client'

import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/px'
import { sampleNoiseFloorDbfs } from './sampleNoiseFloorDbfs'

interface Props {
  onPass: () => void
}

type Status = 'idle' | 'prompting' | 'sampling' | 'ready' | 'denied'

// Threshold for "noisy" environment, in dBFS (decibels relative to full
// scale). Browser-side noiseSuppression is enabled, so the dBFS reading
// reflects post-NS audio: typical quiet rooms read ~-50 to -45, office
// ambient -35 to -30, coffee shops -25 to -20. We warn (not block)
// above -30.
const NOISE_WARN_DBFS = -30

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
        audio: true,
      })
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
            Your environment sounds noisy. The interview will still work,
            but for the cleanest call, find a quieter spot.
          </p>
        )}
      </div>
    </section>
  )
}
