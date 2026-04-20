'use client'

import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { CandidateSessionError } from '@/lib/api/candidate-session'
import { useRequestOtp } from '@/lib/hooks/use-request-otp'
import { useVerifyOtp } from '@/lib/hooks/use-verify-otp'

interface Props {
  token: string
}

function asCandidateError(err: Error): CandidateSessionError | null {
  if (err && typeof err === 'object' && 'status' in err) {
    return err as CandidateSessionError
  }
  return null
}

export function OtpStep({ token }: Props) {
  const [code, setCode] = useState('')
  const [cooldown, setCooldown] = useState(0)
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
      <div className="rounded-lg border border-zinc-200 bg-white p-6">
        <h2 className="text-lg font-semibold">Enter your access code</h2>
        <p className="mt-2 text-sm text-zinc-600">
          Click <strong>Send code</strong> to receive a 6-digit code at your
          email. The code is valid for 10 minutes.
        </p>
        <div className="mt-4 flex items-center gap-3">
          <Button
            variant="outline"
            onClick={onSendCode}
            disabled={cooldown > 0 || requestOtp.isPending}
          >
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
            value={code}
            onChange={(e) =>
              setCode(e.target.value.replace(/\D/g, '').slice(0, 6))
            }
            className="w-32 text-center tracking-widest"
          />
          <Button
            onClick={onVerify}
            disabled={code.length !== 6 || verifyOtp.isPending}
          >
            {verifyOtp.isPending ? 'Verifying…' : 'Verify'}
          </Button>
        </div>
        {attemptsRemaining !== null && (
          <p className="mt-2 text-sm text-red-600">
            {attemptsRemaining === 0
              ? 'No attempts remaining — please request a new code.'
              : `Invalid code. ${attemptsRemaining} attempt${attemptsRemaining === 1 ? '' : 's'} remaining.`}
          </p>
        )}
      </div>
    </section>
  )
}
