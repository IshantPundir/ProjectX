'use client'

import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/px'

interface Props {
  onPass: () => void
}

type Status = 'idle' | 'prompting' | 'ready' | 'denied'

export function CameraMicStep({ onPass }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [status, setStatus] = useState<Status>('idle')
  const [error, setError] = useState<string | null>(null)

  const start = async () => {
    setStatus('prompting')
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: true,
        audio: true,
      })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
      }
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
          className="mb-4 text-[14px]"
          style={{ color: 'var(--px-fg-2)', lineHeight: 1.6 }}
        >
          We&apos;ll access your camera and microphone during the interview.
          Test them now:
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
        <div className="mt-4 flex items-center gap-3">
          {status === 'idle' && (
            <Button onClick={start}>Test camera &amp; mic</Button>
          )}
          {status === 'prompting' && (
            <p className="text-sm" style={{ color: 'var(--px-fg-3)' }}>
              Waiting for permission…
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
      </div>
    </section>
  )
}
