'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import { Button } from '@/components/px'
import { BrandMark } from '@/components/interview/BrandMark'
import { isMultiDisplay, subscribeDisplayChange } from '@/lib/proctoring/displays'
import { usePreCheckFaceGate } from '@/components/interview/proctoring/use-precheck-face-gate'
import { mapBoxToContainer } from '@/components/interview/proctoring/vision/face-box'
import { PreCheckFaceWarning } from './PreCheckFaceWarning'
import { sampleNoiseFloorDbfs } from './sampleNoiseFloorDbfs'

interface Props {
  /** Called when the candidate clicks Start interview. */
  onStart: () => void
  /** When true, surface the single-display warning (non-blocking). */
  proctored?: boolean
}

type Status = 'starting' | 'live' | 'denied'

// dBFS threshold for a "noisy" room (post browser-NS). We warn, never block.
const NOISE_WARN_DBFS = -30
// Smooth Start enable/disable against per-frame detection flicker.
const GATE_DEBOUNCE_MS = 400

/**
 * Immersive, full-bleed camera + mic check. The preview fills the screen; the
 * MediaPipe FaceDetector runs live, marks the candidate's face, and gates the
 * Start button on exactly ONE face. More than one face shows the session-style
 * warning and disables Start until the candidate is alone. Camera + model start
 * automatically. If the model can't load, Start falls back to device-only gating
 * (never a lockout). Devices are released on unmount so LiveKit can re-acquire.
 */
export function ReadyStage({ onStart, proctored = false }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [status, setStatus] = useState<Status>('starting')
  const [error, setError] = useState<string | null>(null)
  const [noiseDbfs, setNoiseDbfs] = useState<number | null>(null)
  const [multiDisplay, setMultiDisplay] = useState<boolean | null>(null)
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 0, h: 0 })

  const face = usePreCheckFaceGate(videoRef, status === 'live')

  // Debounced gate count so Start doesn't flicker with per-frame detection noise.
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
  // effect below doesn't trip react-hooks/set-state-in-effect. The retry handler
  // resets the visible state itself before re-invoking.
  const start = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true })
      streamRef.current = stream
      if (videoRef.current) videoRef.current.srcObject = stream
      setStatus('live')
      // Best-effort noise sample — informational only, does not gate Start.
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

  // Auto-start the camera as soon as the candidate reaches this step (once).
  const startedRef = useRef(false)
  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    void start()
  }, [start])

  // Track the displayed video size so face boxes map correctly under object-cover.
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

  // Release devices on unmount so the live session can re-acquire the camera.
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
  // No lockout: if the model failed/timed out, fall back to device-only gating.
  const canStart = status === 'live' && (face.failed || (face.ready && gateCount === 1))

  let hint = ''
  if (status === 'starting') hint = 'Starting your camera…'
  else if (visionPending) hint = 'Loading the proctoring model…'
  else if (noFace) hint = 'Position your face in the frame to continue.'
  else if (multipleFaces) hint = 'Only you should be on camera.'
  else if (canStart) hint = 'You’re all set.'

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

      {/* Live face marker(s) */}
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

      {/* Top bar — brand + live status */}
      <div className="absolute inset-x-0 top-0 flex items-center justify-between gap-3 p-5">
        <div className="flex items-center gap-2 rounded-full bg-black/45 px-3 py-1.5 backdrop-blur">
          <BrandMark variant="mark" className="h-5 w-5" />
          <span className="text-[12px] font-medium text-white/90">Camera check</span>
        </div>
        {hint && status !== 'denied' && (
          <div
            role="status"
            aria-live="polite"
            className="rounded-full bg-black/45 px-3 py-1.5 text-[12.5px] font-medium text-white/90 backdrop-blur"
          >
            {hint}
          </div>
        )}
      </div>

      {/* Bottom bar — secondary warnings + Start */}
      <div className="absolute inset-x-0 bottom-0 flex flex-col items-center gap-3 p-6">
        {(noisy || displayWarn) && (
          <div className="flex flex-col items-center gap-1 text-center">
            {noisy && (
              <p className="rounded-full bg-black/50 px-3 py-1 text-[12px] font-medium text-white/85 backdrop-blur">
                It sounds noisy — a quieter spot is better.
              </p>
            )}
            {displayWarn && (
              <p className="rounded-full bg-black/50 px-3 py-1 text-[12px] font-medium text-white/85 backdrop-blur">
                More than one display detected — a single screen is recommended.
              </p>
            )}
          </div>
        )}
        <Button
          size="lg"
          onClick={onStart}
          disabled={!canStart}
          aria-disabled={!canStart}
          className="w-full max-w-sm"
        >
          Start interview
        </Button>
      </div>

      {/* Multiple-faces warning (session-style) */}
      {multipleFaces && <PreCheckFaceWarning />}

      {/* Permission denied */}
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
