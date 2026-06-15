'use client'

import { useMemo, useState } from 'react'
import dynamic from 'next/dynamic'

import { APP_CONFIG_DEFAULTS, type AppConfig } from '@/app-config'
import { CompletionScreen } from '@/components/interview/app/CompletionScreen'
import { ProctoringEndedScreen } from '@/components/interview/app/ProctoringEndedScreen'
import { useCandidateSession } from '@/lib/hooks/use-candidate-session'

import { CameraMicStep } from './CameraMicStep'
import { ConsentStep } from './ConsentStep'
import { OtpStep } from './OtpStep'
import { WelcomeStep } from './WelcomeStep'
import { WizardFrame } from './WizardFrame'

const App = dynamic(
  () => import('@/components/interview/app/app').then((m) => m.App),
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

type WizardStepKey = 'consent' | 'otp' | 'cam-mic' | 'error'

export function WizardShell({ token }: { token: string }) {
  const { data, isLoading, error } = useCandidateSession(token)
  const [camMicPassed, setCamMicPassed] = useState(false)
  const [introSeen, setIntroSeen] = useState(false)

  const currentStep = useMemo<WizardStepKey>(() => {
    if (!data) return 'error'
    if (data.state === 'cancelled' || data.state === 'error') return 'error'
    if (data.state === 'created' || data.state === 'pre_check') return 'consent'
    if (data.state === 'consented') {
      if (data.otp_required && !data.otp_verified_at) return 'otp'
      return 'cam-mic'
    }
    return 'cam-mic'
  }, [data])

  const appConfig = useMemo<AppConfig>(
    () =>
      data
        ? {
            ...APP_CONFIG_DEFAULTS,
            companyName: data.company_name,
            pageTitle: `${data.company_name} · Interview`,
          }
        : APP_CONFIG_DEFAULTS,
    [data],
  )

  if (isLoading) {
    return (
      <WizardFrame companyName="" jobTitle="" current="welcome" otpRequired={false}>
        <div
          className="rounded-[14px] border p-6 text-center text-sm"
          style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)', color: 'var(--px-fg-3)' }}
        >
          Loading…
        </div>
      </WizardFrame>
    )
  }

  if (error) {
    return (
      <WizardFrame companyName="" jobTitle="" current="welcome" otpRequired={false}>
        <div
          className="rounded-[14px] border p-8 text-center"
          style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
        >
          <h1 className="px-serif m-0 text-[28px] font-normal" style={{ color: 'var(--px-fg)' }}>
            This link isn&apos;t valid
          </h1>
          <p className="mx-auto mt-3 max-w-sm text-[14px]" style={{ color: 'var(--px-fg-3)', lineHeight: 1.7 }}>
            The invite may have been revoked, replaced, or expired. Please contact the recruiter who sent it.
          </p>
        </div>
      </WizardFrame>
    )
  }

  if (!data) return null

  // Already completed → terminal screen, no rejoin button.
  if (data.state === 'completed') {
    return <CompletionScreen />
  }

  // Ended by proctoring policy → terminal screen (NOT the cam/mic step).
  // The token is consumed, so /start would 409; show why it ended instead.
  if (data.state === 'terminated') {
    return <ProctoringEndedScreen reason={data.proctoring_outcome} />
  }

  // Active session → rejoin path. Bypasses cam-mic + consent (already passed).
  if (data.state === 'active') {
    return <App appConfig={appConfig} token={token} preCheck={data} mode="rejoin" />
  }

  // Cam-mic passed → start path.
  if (currentStep === 'cam-mic' && camMicPassed) {
    return <App appConfig={appConfig} token={token} preCheck={data} mode="start" />
  }

  // Map to the stepper's accepted set: 'error' (cancelled/error session) and the
  // pre-consent welcome gate both render as 'welcome' (no active step).
  const stepperCurrent: 'consent' | 'otp' | 'cam-mic' | 'welcome' =
    currentStep === 'error'
      ? 'welcome'
      : !introSeen && currentStep === 'consent'
        ? 'welcome'
        : currentStep

  return (
    <WizardFrame
      companyName={data.company_name}
      jobTitle={data.job_title}
      current={stepperCurrent}
      otpRequired={data.otp_required}
      accent={appConfig.accent}
    >
      {currentStep === 'consent' && !introSeen && (
        <WelcomeStep durationMinutes={data.duration_minutes} onBegin={() => setIntroSeen(true)} />
      )}
      {currentStep === 'consent' && introSeen && (
        <ConsentStep token={token} consentText={data.consent_text} />
      )}
      {currentStep === 'otp' && <OtpStep token={token} otpIssuedAt={data.otp_issued_at} />}
      {currentStep === 'cam-mic' && !camMicPassed && (
        <CameraMicStep onPass={() => setCamMicPassed(true)} proctored={data.proctoring_enabled} />
      )}
    </WizardFrame>
  )
}
