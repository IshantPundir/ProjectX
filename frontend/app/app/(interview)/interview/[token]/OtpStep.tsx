'use client'

import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/px'
import { Input } from '@/components/px'
import type { CandidateSessionError } from '@/lib/api/candidate-session'
import { useRequestOtp } from '@/lib/hooks/use-request-otp'
import { useVerifyOtp } from '@/lib/hooks/use-verify-otp'

interface Props {
  token: string
  /**
   * Timestamp of the last OTP issuance from GET /pre-check.
   * Used to restore the 60s [Send code] cooldown on page reload so the
   * visible timer matches the server-side rate limit.
   */
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
  const elapsed = Math.floor(
    (Date.now() - new Date(otpIssuedAt).getTime()) / 1000,
  )
  return Math.max(0, 60 - elapsed)
}

export function OtpStep({ token, otpIssuedAt }: Props) {
  const [code, setCode] = useState('')
  const [cooldown, setCooldown] = useState(() => initialCooldown(otpIssuedAt))
  const [attemptsRemaining, setAttemptsRemaining] = useState<number | null>(
    null,
  )
  const requestOtp = useRequestOtp(token)
  const verifyOtp = useVerifyOtp(token)

  useEffect(() => {
    if (cooldown <= 0) return
    const timer = setInterval(() => {
      setCooldown((n) => Math.max(0, n - 1))
    }, 1000)
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
          Verify identity
        </div>
        <h2
          className="px-serif m-0 mb-2 text-[24px] font-normal"
          style={{ letterSpacing: '-0.4px', color: 'var(--px-fg)' }}
        >
          Enter your access code
        </h2>
        <p
          className="mb-4 text-[14px]"
          style={{ color: 'var(--px-fg-2)', lineHeight: 1.6 }}
        >
          Click <strong>Send code</strong> to receive a 6-digit code at your
          email. The code is valid for 10 minutes.
        </p>
        <div className="mb-4 flex items-center gap-3">
          <Button
            variant="outline"
            onClick={onSendCode}
            disabled={cooldown > 0 || requestOtp.isPending}
          >
            {cooldown > 0 ? `Resend in ${cooldown}s` : 'Send code'}
          </Button>
        </div>
        <div className="flex items-center gap-3">
          <Input
            type="text"
            inputMode="numeric"
            pattern="\d*"
            maxLength={6}
            placeholder="123456"
            value={code}
            onChange={(e) =>
              setCode(e.target.value.replace(/\D/g, '').slice(0, 6))
            }
            className="px-input mono w-36 text-center tracking-widest"
          />
          <Button
            onClick={onVerify}
            disabled={code.length !== 6 || verifyOtp.isPending}
          >
            {verifyOtp.isPending ? 'Verifying…' : 'Verify'}
          </Button>
        </div>
        {attemptsRemaining !== null && (
          <p className="mt-2 text-sm" style={{ color: 'var(--px-danger)' }}>
            {attemptsRemaining === 0
              ? 'No attempts remaining — please request a new code.'
              : `Invalid code. ${attemptsRemaining} attempt${attemptsRemaining === 1 ? '' : 's'} remaining.`}
          </p>
        )}
      </div>
    </section>
  )
}
