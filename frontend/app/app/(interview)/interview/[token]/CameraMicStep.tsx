'use client'

import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'

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
      <div className="rounded-lg border border-zinc-200 bg-white p-6">
        <h2 className="text-lg font-semibold">Camera &amp; microphone check</h2>
        <p className="mt-2 text-sm text-zinc-600">
          We&apos;ll access your camera and microphone during the interview.
          Test them now:
        </p>
        <div className="mt-4 aspect-video w-full bg-zinc-900 rounded overflow-hidden">
          <video
            ref={videoRef}
            autoPlay
            muted
            playsInline
            className="w-full h-full object-cover"
          />
        </div>
        <div className="mt-4 flex items-center gap-3">
          {status === 'idle' && (
            <Button onClick={start}>Test camera &amp; mic</Button>
          )}
          {status === 'prompting' && (
            <p className="text-sm text-zinc-500">Waiting for permission…</p>
          )}
          {status === 'ready' && (
            <>
              <span className="text-sm text-green-700">
                Camera and mic are working ✓
              </span>
              <Button onClick={onPass}>Continue</Button>
            </>
          )}
          {status === 'denied' && (
            <>
              <span className="text-sm text-red-600">{error}</span>
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
