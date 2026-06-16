'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/px'
import { BrandMark } from '@/components/interview/BrandMark'
import { isMultiDisplay, subscribeDisplayChange } from '@/lib/proctoring/displays'
import { usePreCheckFaceGate } from '@/components/interview/proctoring/use-precheck-face-gate'
import { mapBoxToContainer } from '@/components/interview/proctoring/vision/face-box'
import { useFullscreenLock } from '@/hooks/use-fullscreen-lock'
import { candidateSessionApi } from '@/lib/api/candidate-session'
import { captureVideoFrame } from '@/lib/capture-frame'
import { CaptureCountdown } from './CaptureCountdown'
import { PreCheckFaceWarning } from './PreCheckFaceWarning'
import { sampleNoiseFloorDbfs } from './sampleNoiseFloorDbfs'

interface Props {
  /** Candidate JWT (path token) -- used to upload the reference photo. */
  token: string
  /** Called once the reference photo is captured + uploaded and we may start. */
  onStart: () => void
  /** When true, surface the single-display warning (non-blocking). */
  proctored?: boolean
}

type Status = 'starting' | 'live' | 'denied'
type CapturePhase = 'idle' | 'counting' | 'uploading' | 'failed'

const NOISE_WARN_DBFS = -30
const GATE_DEBOUNCE_MS = 400

/**
 * Immersive, full-bleed camera + mic check with live face detection. Clicking
 * "Start interview" runs a 3-2-1 countdown, captures a high-quality still, and
 * uploads it (blocking -- retry on failure) before the session starts. The
 * countdown aborts if proctoring is violated (face count != 1, fullscreen exit,
 * minimize, tab switch, focus loss). Camera + model start automatically; devices
 * are released on unmount so LiveKit can re-acquire.
 */
export function ReadyStage({ token, onStart, proctored = false }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const blobRef = useRef<Blob | null>(null)
  const [status, setStatus] = useState<Status>('starting')
  const [error, setError] = useState<string | null>(null)
  const [noiseDbfs, setNoiseDbfs] = useState<number | null>(null)
  const [multiDisplay, setMultiDisplay] = useState<boolean | null>(null)
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 0, h: 0 })
  const [phase, setPhase] = useState<CapturePhase>('idle')
  const [abortNote, setAbortNote] = useState(false)

  const { locked } = useFullscreenLock()
  const face = usePreCheckFaceGate(videoRef, status === 'live')

  const [gateCount, setGateCount] = useState(0)
  useEffect(() => {
    const t = window.setTimeout(() => setGateCount(face.faceCount), GATE_DEBOUNCE_MS)
    return () => window.clearTimeout(t)
  }, [face.faceCount])

  useEffect(() => {
    if (!proctored) return
    const refresh = () => setMultiDisplay(isMultiDisplay())
    refresh()
    return subscribeDisplayChange(refresh)
  }, [proctored])

  // No synchronous setState here (the first statement awaits) so the auto-start
  // effect below doesn't trip react-hooks/set-state-in-effect.
  const start = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
      streamRef.current = stream
      if (videoRef.current) videoRef.current.srcObject = stream
      setStatus('live')
      sampleNoiseFloorDbfs(stream)
        .then(setNoiseDbfs)
        .catch(() => {})
    } catch (err) {
      const name = (err as Error).name
      if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
        setError(
          'Camera and microphone permission denied. Please enable them in your browser settings.',
        )
      } else if (name === 'NotFoundError') {
        setError('No camera or microphone detected on this device.')
      } else {
        setError((err as Error).message)
      }
      setStatus('denied')
    }
  }, [])

  const retry = useCallback(() => {
    setStatus('starting')
    setError(null)
    setNoiseDbfs(null)
    void start()
  }, [start])

  const startedRef = useRef(false)
  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    void start()
  }, [start])

  useEffect(() => {
    const el = videoRef.current
    if (!el) return
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight })
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [status])

  useEffect(() => {
    return () => {
      streamRef.current?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  const noisy = noiseDbfs !== null && noiseDbfs > NOISE_WARN_DBFS
  const displayWarn = proctored && multiDisplay === true

  const visionPending = status === 'live' && !face.ready && !face.failed
  const multipleFaces = face.ready && gateCount >= 2
  const noFace = face.ready && gateCount === 0
  const canStart = status === 'live' && (face.failed || (face.ready && gateCount === 1))

  // Capture must stay stable: fullscreen+visible+focused, live, and (when vision
  // works) exactly one face. If this drops mid-countdown, the capture aborts.
  const captureStable = locked && status === 'live' && (!face.ready || gateCount === 1)

  const upload = useCallback(async () => {
    const blob = blobRef.current
    if (!blob) {
      setPhase('failed')
      return
    }
    setPhase('uploading')
    try {
      await candidateSessionApi.uploadReferencePhoto(token, blob)
      onStart()
    } catch {
      setPhase('failed')
    }
  }, [token, onStart])

  const onCountdownComplete = useCallback(async () => {
    const video = videoRef.current
    if (!video) {
      setPhase('idle')
      setAbortNote(true)
      return
    }
    try {
      blobRef.current = await captureVideoFrame(video)
      await upload()
    } catch {
      setPhase('idle')
      setAbortNote(true)
    }
  }, [upload])

  const onCountdownAbort = useCallback(() => {
    setPhase('idle')
    setAbortNote(true)
  }, [])

  const beginCapture = useCallback(() => {
    setAbortNote(false)
    setPhase('counting')
  }, [])

  let hint = ''
  if (status === 'starting') hint = 'Starting your camera…'
  else if (visionPending) hint = 'Loading the proctoring model…'
  else if (noFace) hint = 'Position your face in the frame to continue.'
  else if (multipleFaces) hint = 'Only you should be on camera.'
  else if (canStart) hint = "You're all set."

  const boxTone = multipleFaces ? 'caution' : 'ok'

  return (
    <div className="relative min-h-dvh w-full overflow-hidden bg-black">
      <video
        ref={videoRef}
        autoPlay
        muted
        playsInline
        className="absolute inset-0 h-full w-full object-cover [transform:scaleX(-1)]"
      />

      {face.ready &&
        face.frame &&
        size.w > 0 &&
        face.boxes.map((b, i) => {
          const s = mapBoxToContainer(b, face.frame!.width, face.frame!.height, size.w, size.h, true)
          if (!s) return null
          return (
            <div
              key={i}
              aria-hidden
              className="pointer-events-none absolute rounded-[16px] border-2 transition-all duration-150"
              style={{
                left: s.left,
                top: s.top,
                width: s.width,
                height: s.height,
                borderColor: boxTone === 'caution' ? 'var(--px-caution)' : 'var(--px-ok)',
                boxShadow: '0 0 0 2px rgba(0,0,0,0.35), 0 6px 24px rgba(0,0,0,0.35)',
              }}
            />
          )
        })}

      <div className="absolute inset-x-0 top-0 flex items-center justify-between gap-3 p-5">
        <div className="flex items-center gap-2 rounded-full bg-black/45 px-3 py-1.5 backdrop-blur">
          <BrandMark variant="mark" className="h-5 w-5" />
          <span className="text-[12px] font-medium text-white/90">Camera check</span>
        </div>
        {hint && status !== 'denied' && phase === 'idle' && (
          <div
            role="status"
            aria-live="polite"
            className="rounded-full bg-black/45 px-3 py-1.5 text-[12.5px] font-medium text-white/90 backdrop-blur"
          >
            {hint}
          </div>
        )}
      </div>

      <div className="absolute inset-x-0 bottom-0 flex flex-col items-center gap-3 p-6">
        {abortNote && phase === 'idle' && (
          <p className="rounded-full bg-black/55 px-4 py-1.5 text-[12.5px] font-medium text-px-caution backdrop-blur">
            {"Let's try again — stay alone, in frame, and in fullscreen."}
          </p>
        )}
        {(noisy || displayWarn) && phase === 'idle' && (
          <div className="flex flex-col items-center gap-1 text-center">
            {noisy && (
              <p className="rounded-full bg-black/50 px-3 py-1 text-[12px] font-medium text-white/85 backdrop-blur">
                {"It sounds noisy — a quieter spot is better."}
              </p>
            )}
            {displayWarn && (
              <p className="rounded-full bg-black/50 px-3 py-1 text-[12px] font-medium text-white/85 backdrop-blur">
                {"More than one display detected — a single screen is recommended."}
              </p>
            )}
          </div>
        )}
        <Button
          size="lg"
          onClick={beginCapture}
          disabled={!canStart || phase !== 'idle'}
          aria-disabled={!canStart || phase !== 'idle'}
          className="w-full max-w-sm"
        >
          Start interview
        </Button>
      </div>

      {multipleFaces && phase === 'idle' && <PreCheckFaceWarning />}

      {phase === 'counting' && (
        <CaptureCountdown
          unstable={!captureStable}
          onComplete={onCountdownComplete}
          onAbort={onCountdownAbort}
        />
      )}

      {phase === 'uploading' && (
        <div className="absolute inset-0 z-[25] grid place-items-center bg-black/55 backdrop-blur-md">
          <p
            role="status"
            aria-live="polite"
            className="rounded-full bg-black/45 px-5 py-2 text-[14px] font-medium text-white/90 backdrop-blur"
          >
            Saving your photo…
          </p>
        </div>
      )}

      {phase === 'failed' && (
        <div className="absolute inset-0 z-[30] grid place-items-center bg-black/80 p-6 text-center backdrop-blur-xl">
          <div className="px-glass-strong max-w-md rounded-2xl px-8 py-10">
            <h2 className="px-serif text-2xl font-normal text-px-fg">{"Couldn't save your photo"}</h2>
            <p className="mt-3 text-sm leading-relaxed text-px-fg-3">
              We need a clear photo before your interview can start. Please try again.
            </p>
            <Button size="lg" onClick={upload} className="mt-6">
              Try again
            </Button>
          </div>
        </div>
      )}

      {status === 'denied' && (
        <div className="absolute inset-0 z-[30] grid place-items-center bg-black/85 p-6 text-center backdrop-blur-xl">
          <div className="px-glass-strong max-w-md rounded-2xl px-8 py-10">
            <h2 className="px-serif text-2xl font-normal text-px-fg">Camera access needed</h2>
            <p className="mt-3 text-sm leading-relaxed text-px-fg-3">{error}</p>
            <Button variant="outline" size="lg" onClick={retry} className="mt-6">
              Try again
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
