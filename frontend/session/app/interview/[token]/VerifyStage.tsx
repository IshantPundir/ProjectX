'use client'

import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button, Input } from '@/components/px'
import type { CandidateSessionError } from '@/lib/api/candidate-session'
import { useRequestOtp } from '@/lib/hooks/use-request-otp'
import { useVerifyOtp } from '@/lib/hooks/use-verify-otp'

interface Props {
  token: string
  /** Last OTP issuance from /pre-check -- restores the 60s cooldown on reload. */
  otpIssuedAt: string | null
}

function asCandidateError(err: Error): CandidateSessionError | null {
  if (err && typeof err === 'object' && 'status' in err) {
    return err as CandidateSessionError
  }
  return null
}

function initialCooldown(otpIssuedAt: string | null): number {
  if (!otpIssuedAt) return 0
  const elapsed = Math.floor((Date.now() - new Date(otpIssuedAt).getTime()) / 1000)
  return Math.max(0, 60 - elapsed)
}

export function VerifyStage({ token, otpIssuedAt }: Props) {
  const [code, setCode] = useState('')
  const [cooldown, setCooldown] = useState(() => initialCooldown(otpIssuedAt))
  const [attemptsRemaining, setAttemptsRemaining] = useState<number | null>(null)
  const requestOtp = useRequestOtp(token)
  const verifyOtp = useVerifyOtp(token)

  useEffect(() => {
    if (cooldown <= 0) return
    const timer = setInterval(() => setCooldown((n) => Math.max(0, n - 1)), 1000)
    return () => clearInterval(timer)
  }, [cooldown])

  const onSendCode = () => {
    requestOtp.mutate(undefined, {
      onSuccess: () => {
        toast.success('Code sent to your email')
        setCooldown(60)
        setAttemptsRemaining(null)
      },
      onError: (err) => {
        const ce = asCandidateError(err)
        if (ce?.retry_after_seconds) setCooldown(ce.retry_after_seconds)
        toast.error(err.message)
      },
    })
  }

  const onVerify = () => {
    verifyOtp.mutate(
      { code },
      {
        onSuccess: () => {
          toast.success('Verified')
          setAttemptsRemaining(null)
        },
        onError: (err) => {
          const ce = asCandidateError(err)
          if (ce && typeof ce.attempts_remaining === 'number') {
            setAttemptsRemaining(ce.attempts_remaining)
          }
          toast.error(err.message)
        },
      },
    )
  }

  return (
    <div className="mx-auto w-full max-w-md">
      <p className="text-[11px] font-semibold uppercase tracking-[1.2px] text-px-fg-4">
        Verify identity
      </p>
      <h1 className="px-serif mt-1.5 text-[clamp(24px,5vw,30px)] font-normal tracking-[-0.4px] text-px-fg">
        Enter your access code
      </h1>
      <p className="mt-2 text-[14.5px] leading-relaxed text-px-fg-2">
        Tap <strong>Send code</strong> to get a 6-digit code by email. It is valid for 10 minutes.
      </p>

      <div className="mt-5 flex items-center gap-3">
        <Button variant="outline" onClick={onSendCode} disabled={cooldown > 0 || requestOtp.isPending}>
          {cooldown > 0 ? `Resend in ${cooldown}s` : 'Send code'}
        </Button>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <Input
          type="text"
          inputMode="numeric"
          pattern="\d*"
          maxLength={6}
          placeholder="123456"
          aria-label="6-digit access code"
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
          className="px-input mono w-40 text-center text-lg tracking-[0.4em]"
        />
        <Button onClick={onVerify} disabled={code.length !== 6 || verifyOtp.isPending}>
          {verifyOtp.isPending ? 'Verifying…' : 'Verify'}
        </Button>
      </div>

      {attemptsRemaining !== null && (
        <p className="mt-2 text-sm text-px-danger" role="alert" aria-live="polite">
          {attemptsRemaining === 0
            ? 'No attempts remaining — please request a new code.'
            : `Invalid code. ${attemptsRemaining} attempt${attemptsRemaining === 1 ? '' : 's'} remaining.`}
        </p>
      )}
    </div>
  )
}
