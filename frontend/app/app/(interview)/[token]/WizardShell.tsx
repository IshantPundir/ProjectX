'use client'

import { useMemo } from 'react'

import { useCandidateSession } from '@/lib/hooks/use-candidate-session'

import { ConsentStep } from './ConsentStep'
import { OtpStep } from './OtpStep'

type WizardStepKey =
  | 'consent'
  | 'otp'
  | 'cam-mic'
  | 'start'
  | 'already-started'
  | 'error'

export function WizardShell({ token }: { token: string }) {
  const { data, isLoading, error } = useCandidateSession(token)

  const currentStep = useMemo<WizardStepKey>(() => {
    if (!data) return 'error'
    if (data.state === 'active') return 'already-started'
    if (data.state === 'cancelled' || data.state === 'error') return 'error'
    if (data.state === 'created' || data.state === 'pre_check') return 'consent'
    if (data.state === 'consented') {
      if (data.otp_required && !data.otp_verified_at) return 'otp'
      return 'cam-mic'
    }
    return 'error'
  }, [data])

  if (isLoading) {
    return <p className="text-zinc-500">Loading…</p>
  }

  if (error) {
    return (
      <div className="text-center py-12">
        <h1 className="text-xl font-semibold">This link isn&apos;t valid</h1>
        <p className="mt-3 text-sm text-zinc-600">
          The invite may have been revoked, replaced, or expired. Please contact
          the recruiter who sent it.
        </p>
      </div>
    )
  }

  if (!data) return null

  return (
    <div>
      <header className="mb-8">
        <div className="text-xs uppercase tracking-wider text-zinc-500">
          Pre-interview check
        </div>
        <h1 className="mt-1 text-2xl font-semibold">
          {data.job_title} · {data.stage_name}
        </h1>
        <p className="mt-1 text-sm text-zinc-600">
          {data.company_name} · {data.duration_minutes} minutes
        </p>
        <StepIndicator current={currentStep} otpRequired={data.otp_required} />
      </header>

      {currentStep === 'consent' && (
        <ConsentStep token={token} consentText={data.consent_text} />
      )}
      {currentStep === 'otp' && <OtpStep token={token} />}
      {currentStep === 'cam-mic' && <CameraMicStepPlaceholder token={token} />}
      {currentStep === 'start' && <StartStepPlaceholder token={token} />}
      {currentStep === 'already-started' && <AlreadyStartedPanel />}
    </div>
  )
}

function StepIndicator({
  current,
  otpRequired,
}: {
  current: WizardStepKey
  otpRequired: boolean
}) {
  const steps: { key: WizardStepKey; label: string }[] = [
    { key: 'consent', label: 'Consent' },
    ...(otpRequired ? [{ key: 'otp' as const, label: 'Verify identity' }] : []),
    { key: 'cam-mic', label: 'Camera & mic' },
    { key: 'start', label: 'Start' },
  ]
  const currentIdx = steps.findIndex((s) => s.key === current)
  return (
    <ol className="mt-4 flex gap-2 text-xs text-zinc-500">
      {steps.map((s, i) => (
        <li
          key={s.key}
          className={i <= currentIdx ? 'text-zinc-900 font-medium' : ''}
        >
          {i + 1}. {s.label}
          {i < steps.length - 1 && (
            <span className="mx-1 text-zinc-300">→</span>
          )}
        </li>
      ))}
    </ol>
  )
}

function CameraMicStepPlaceholder({ token: _t }: { token: string }) {
  return <p>Camera/mic step (Task 3C.2.6)</p>
}
function StartStepPlaceholder({ token: _t }: { token: string }) {
  return <p>Start step (Task 3C.2.7)</p>
}
function AlreadyStartedPanel() {
  return (
    <div className="rounded-lg bg-zinc-100 p-6 text-center">
      <h2 className="text-lg font-semibold">Your session has already started</h2>
      <p className="mt-2 text-sm text-zinc-600">
        If you were disconnected, the rejoin flow will be available in the next
        release.
      </p>
    </div>
  )
}
