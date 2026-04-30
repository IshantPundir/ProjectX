'use client'

import { useMemo, useState } from 'react'

import dynamic from 'next/dynamic'

import type { StartSessionResponse } from '@/lib/api/candidate-session'
import { useCandidateSession } from '@/lib/hooks/use-candidate-session'

import { CameraMicStep } from './CameraMicStep'
import { ConsentStep } from './ConsentStep'
import { OtpStep } from './OtpStep'
import { StartStep } from './StartStep'

const LiveSessionShell = dynamic(
  () =>
    import('./LiveSession/LiveSessionShell').then((m) => m.LiveSessionShell),
  {
    ssr: false,
    loading: () => (
      <div
        className="grid min-h-screen place-items-center text-[14px]"
        style={{ color: 'var(--px-fg-2)' }}
      >
        Connecting…
      </div>
    ),
  },
)

type WizardStepKey =
  | 'consent'
  | 'otp'
  | 'cam-mic'
  | 'start'
  | 'already-started'
  | 'error'

export function WizardShell({ token }: { token: string }) {
  const { data, isLoading, error } = useCandidateSession(token)
  const [camMicPassed, setCamMicPassed] = useState(false)
  const [creds, setCreds] = useState<StartSessionResponse | null>(null)

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
    return (
      <WizardFrame companyName="" jobTitle="" stageName="">
        <p className="text-center text-sm" style={{ color: 'var(--px-fg-3)' }}>
          Loading…
        </p>
      </WizardFrame>
    )
  }

  if (error) {
    return (
      <WizardFrame companyName="" jobTitle="" stageName="">
        <div className="mx-auto max-w-[600px] py-16 text-center">
          <h1
            className="px-serif m-0 text-[40px] font-normal"
            style={{ letterSpacing: '-1px', color: 'var(--px-fg)' }}
          >
            This link isn&apos;t valid
          </h1>
          <p
            className="mx-auto mt-4 max-w-md text-[15px]"
            style={{ color: 'var(--px-fg-2)', lineHeight: 1.7 }}
          >
            The invite may have been revoked, replaced, or expired. Please
            contact the recruiter who sent it.
          </p>
        </div>
      </WizardFrame>
    )
  }

  if (!data) return null

  // `creds` is set only by the StartStep success path (useStartSession resolves
  // 200). At that point the backend has atomically transitioned state to
  // 'active', but the cached /pre-check data updates via the mutation's
  // setQueryData hook on a separate notify path, so adding && data.state ===
  // 'active' here would introduce a one-render flash where the cached state
  // has flipped but local creds haven't yet -- briefly routing through
  // <AlreadyStartedPanel>. Trust creds alone.
  if (creds) {
    return (
      <LiveSessionShell
        livekitUrl={creds.livekit_url}
        livekitToken={creds.livekit_token}
        roomName={creds.room_name}
      />
    )
  }

  return (
    <WizardFrame
      companyName={data.company_name}
      jobTitle={data.job_title}
      stageName={data.stage_name}
    >
      <StepProgress current={currentStep} otpRequired={data.otp_required} />

      <div className="mb-2 text-[11px] font-semibold uppercase" style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}>
        {data.stage_name} · {data.duration_minutes} minutes
      </div>
      <h1
        className="px-serif m-0 mb-4 text-[44px] font-normal"
        style={{ letterSpacing: '-1.1px', lineHeight: 1.08, color: 'var(--px-fg)' }}
      >
        Pre-interview check
      </h1>
      <p
        className="mb-8 text-[15px]"
        style={{ color: 'var(--px-fg-2)', lineHeight: 1.7 }}
      >
        A few quick steps so we know you&apos;re ready and your setup works.
        Take your time — you can only move forward once each step is complete.
      </p>

      {currentStep === 'consent' && (
        <ConsentStep token={token} consentText={data.consent_text} />
      )}
      {currentStep === 'otp' && (
        <OtpStep token={token} otpIssuedAt={data.otp_issued_at} />
      )}
      {currentStep === 'cam-mic' && !camMicPassed && (
        <CameraMicStep onPass={() => setCamMicPassed(true)} />
      )}
      {currentStep === 'cam-mic' && camMicPassed && (
        <StartStep token={token} onStarted={setCreds} />
      )}
      {currentStep === 'already-started' && <AlreadyStartedPanel />}
    </WizardFrame>
  )
}

function WizardFrame({
  companyName,
  jobTitle,
  stageName: _stageName,
  children,
}: {
  companyName: string
  jobTitle: string
  stageName: string
  children: React.ReactNode
}) {
  return (
    <div className="flex min-h-screen flex-col">
      {/* Top bar — minimal, candidate-facing */}
      <div
        className="flex h-14 flex-shrink-0 items-center gap-3 border-b px-8"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <div
          className="flex h-6 w-6 items-center justify-center rounded-[5px]"
          style={{ background: 'var(--px-accent)' }}
          aria-hidden="true"
        >
          <svg width="11" height="11" viewBox="0 0 12 12">
            <path d="M3 2v8l5-4z" fill="#fff" />
          </svg>
        </div>
        <div className="text-[13px]" style={{ color: 'var(--px-fg)' }}>
          <b style={{ fontWeight: 600 }}>{companyName || 'ProjectX'}</b>
          {jobTitle && (
            <span style={{ color: 'var(--px-fg-4)' }}> · {jobTitle}</span>
          )}
        </div>
        <div className="flex-1" />
      </div>

      <div className="flex-1 overflow-auto px-8 py-12">
        <div className="mx-auto max-w-[640px]">{children}</div>
      </div>
    </div>
  )
}

function StepProgress({
  current,
  otpRequired,
}: {
  current: WizardStepKey
  otpRequired: boolean
}) {
  const steps: { key: WizardStepKey; label: string }[] = [
    { key: 'consent', label: 'Consent' },
    ...(otpRequired ? [{ key: 'otp' as const, label: 'Verify' }] : []),
    { key: 'cam-mic', label: 'Camera & mic' },
    { key: 'start', label: 'Start' },
  ]
  const currentIdx = steps.findIndex((s) => s.key === current)

  return (
    <div className="mb-12 flex gap-2">
      {steps.map((s, i) => {
        const done = i < currentIdx
        const active = i === currentIdx
        return (
          <div key={s.key} className="flex-1">
            <div
              className="h-[3px] rounded-[2px]"
              style={{
                background: done
                  ? 'var(--px-ok)'
                  : active
                    ? 'var(--px-accent)'
                    : 'var(--px-surface-3)',
              }}
            />
            <div
              className="mt-2 text-[11.5px]"
              style={{
                color: active ? 'var(--px-fg)' : 'var(--px-fg-4)',
                fontWeight: active ? 500 : 400,
              }}
            >
              {s.label}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function AlreadyStartedPanel() {
  return (
    <div
      className="rounded-[12px] border p-8 text-center"
      style={{
        background: 'var(--px-surface)',
        borderColor: 'var(--px-hairline)',
      }}
    >
      <h2
        className="px-serif m-0 mb-3 text-[26px] font-normal"
        style={{ color: 'var(--px-fg)' }}
      >
        Your session has already started
      </h2>
      <p className="text-sm" style={{ color: 'var(--px-fg-2)' }}>
        If you were disconnected, the rejoin flow will be available in the next
        release.
      </p>
    </div>
  )
}
