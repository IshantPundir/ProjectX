'use client'

import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/px'
import { isMultiDisplay, subscribeDisplayChange } from '@/lib/proctoring/displays'
import { sampleNoiseFloorDbfs } from './sampleNoiseFloorDbfs'

interface Props {
  /** Called when the candidate clicks Start after devices pass. */
  onStart: () => void
  /** When true, surface the single-display warning (non-blocking). */
  proctored?: boolean
}

type Status = 'idle' | 'prompting' | 'sampling' | 'ready' | 'denied'

// dBFS threshold for a "noisy" room (post browser-NS). We warn, never block.
const NOISE_WARN_DBFS = -30

export function ReadyStage({ onStart, proctored = false }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [status, setStatus] = useState<Status>('idle')
  const [error, setError] = useState<string | null>(null)
  const [noiseDbfs, setNoiseDbfs] = useState<number | null>(null)
  const [multiDisplay, setMultiDisplay] = useState<boolean | null>(null)

  useEffect(() => {
    if (!proctored) return
    const refresh = () => setMultiDisplay(isMultiDisplay())
    refresh()
    return subscribeDisplayChange(refresh)
  }, [proctored])

  const displayWarn = proctored && multiDisplay === true

  const start = async () => {
    setStatus('prompting')
    setError(null)
    setNoiseDbfs(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
      streamRef.current = stream
      if (videoRef.current) videoRef.current.srcObject = stream
      setStatus('sampling')
      const dbfs = await sampleNoiseFloorDbfs(stream)
      setNoiseDbfs(dbfs)
      setStatus('ready')
    } catch (err) {
      const name = (err as Error).name
      if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
        setError('Permission denied. Please enable camera and microphone in your browser settings.')
      } else if (name === 'NotFoundError') {
        setError('No camera or microphone detected on this device.')
      } else {
        setError((err as Error).message)
      }
      setStatus('denied')
    }
  }

  // Release devices when leaving this stage so LiveKit can re-acquire them.
  useEffect(() => {
    return () => {
      streamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  const noisy = noiseDbfs !== null && noiseDbfs > NOISE_WARN_DBFS

  return (
    <div className="mx-auto w-full max-w-lg">
      <p className="text-[11px] font-semibold uppercase tracking-[1.2px] text-px-fg-4">
        Camera &amp; microphone
      </p>
      <h1 className="px-serif mt-1.5 text-[clamp(24px,5vw,32px)] font-normal tracking-[-0.4px] text-px-fg">
        Let&apos;s check your setup
      </h1>
      <p className="mt-2 text-[14.5px] leading-relaxed text-px-fg-2">
        We&apos;ll use your camera and microphone during the interview. Headphones are recommended for the cleanest call.
      </p>

      <div className="mt-5 aspect-video w-full overflow-hidden rounded-2xl border border-px-hairline bg-black/85">
        <video ref={videoRef} autoPlay muted playsInline className="h-full w-full object-cover" />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        {status === 'idle' && <Button onClick={start}>Test camera &amp; mic</Button>}
        {status === 'prompting' && (
          <p className="text-sm text-px-fg-3">Waiting for permission…</p>
        )}
        {status === 'sampling' && (
          <p className="text-sm text-px-fg-3">Listening for background noise (stay quiet for a moment)…</p>
        )}
        {status === 'ready' && (
          <>
            <span className="text-sm font-medium text-px-ok">Camera and mic are working ✓</span>
            <Button size="lg" onClick={onStart}>
              Start <span aria-hidden>&#8594;</span>
            </Button>
          </>
        )}
        {status === 'denied' && (
          <>
            <span className="text-sm text-px-danger">{error}</span>
            <Button variant="outline" onClick={start}>
              Retry
            </Button>
          </>
        )}
      </div>

      {status === 'ready' && (noisy || displayWarn) && (
        <div role="status" className="mt-3 space-y-2">
          {noisy && (
            <p className="text-[13px] leading-relaxed text-px-caution">
              Your environment sounds noisy. The interview will still work, but for the cleanest call, find a quieter spot.
            </p>
          )}
          {displayWarn && (
            <p className="text-[13px] leading-relaxed text-px-caution">
              We detected more than one display. A single screen is recommended — using multiple displays is flagged during the interview.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
