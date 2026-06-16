// app/interview/[token]/WizardShell.tsx
'use client'

import { useMemo, useState } from 'react'
import dynamic from 'next/dynamic'

import { APP_CONFIG_DEFAULTS, type AppConfig } from '@/app-config'
import { CompletionScreen } from '@/components/interview/app/CompletionScreen'
import { ProctoringEndedScreen } from '@/components/interview/app/ProctoringEndedScreen'
import { useCandidateSession } from '@/lib/hooks/use-candidate-session'

import { PreCheckLockGate } from './PreCheckLockGate'
import { IntroStage } from './IntroStage'
import { ReadyStage } from './ReadyStage'
import { StageTransition } from './StageTransition'
import { VerifyStage } from './VerifyStage'
import { WizardFrame } from './WizardFrame'

const App = dynamic(() => import('@/components/interview/app/app').then((m) => m.App), {
  ssr: false,
  loading: () => (
    <div className="grid min-h-dvh place-items-center text-[14px] text-px-fg-3">Connecting…</div>
  ),
})

type Stage = 'intro' | 'verify' | 'ready'

export function WizardShell({ token }: { token: string }) {
  const { data, isLoading, error } = useCandidateSession(token)
  const [camMicPassed, setCamMicPassed] = useState(false)

  const stage = useMemo<Stage>(() => {
    if (!data) return 'intro'
    if (data.state === 'created' || data.state === 'pre_check') return 'intro'
    if (data.state === 'consented') {
      if (data.otp_required && !data.otp_verified_at) return 'verify'
      return 'ready'
    }
    return 'ready'
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

  // The progress indicator: OTP only contributes a step when required.
  const { steps, currentIndex } = useMemo(() => {
    const otp = !!data?.otp_required
    const labels = otp ? ['Welcome', 'Verify', 'Ready'] : ['Welcome', 'Ready']
    const idx = stage === 'intro' ? 0 : stage === 'verify' ? 1 : labels.length - 1
    return { steps: labels, currentIndex: idx }
  }, [data?.otp_required, stage])

  if (isLoading) {
    return (
      <WizardFrame companyName="" jobTitle="" steps={['Welcome', 'Ready']} currentIndex={0}>
        <div className="px-glass mx-auto max-w-md rounded-2xl p-6 text-center text-sm text-px-fg-3">
          Loading…
        </div>
      </WizardFrame>
    )
  }

  if (error) {
    return (
      <WizardFrame companyName="" jobTitle="" steps={['Welcome', 'Ready']} currentIndex={0}>
        <div className="px-glass mx-auto max-w-md rounded-2xl p-8 text-center">
          <h1 className="px-serif m-0 text-[28px] font-normal text-px-fg">This link isn&rsquo;t valid</h1>
          <p className="mx-auto mt-3 max-w-sm text-[14px] leading-relaxed text-px-fg-3">
            The invite may have been revoked, replaced, or expired. Please contact the recruiter who sent it.
          </p>
        </div>
      </WizardFrame>
    )
  }

  if (!data) return null

  if (data.state === 'completed') return <CompletionScreen />
  if (data.state === 'terminated') return <ProctoringEndedScreen reason={data.proctoring_outcome} />
  if (data.state === 'cancelled' || data.state === 'error') {
    return (
      <WizardFrame
        companyName={data.company_name}
        jobTitle={data.job_title}
        steps={steps}
        currentIndex={0}
        accent={appConfig.accent}
      >
        <div className="px-glass mx-auto max-w-md rounded-2xl p-8 text-center">
          <h1 className="px-serif m-0 text-[28px] font-normal text-px-fg">This session has ended</h1>
          <p className="mx-auto mt-3 max-w-sm text-[14px] leading-relaxed text-px-fg-3">
            This interview link is no longer active. Please contact the recruiter who sent it.
          </p>
        </div>
      </WizardFrame>
    )
  }

  // Active session → rejoin path (bypasses pre-check; already consented).
  if (data.state === 'active') {
    return <App appConfig={appConfig} token={token} preCheck={data} mode="rejoin" />
  }

  // Ready + devices passed → start path with autoStart (no redundant WelcomeView).
  if (stage === 'ready' && camMicPassed) {
    return <App appConfig={appConfig} token={token} preCheck={data} mode="start" autoStart />
  }

  // The camera step is its own immersive, full-bleed view (a transform-animated
  // ancestor would break the full-screen video), so it renders outside the
  // WizardFrame chrome + StageTransition — still inside the fullscreen lock.
  if (stage === 'ready') {
    return (
      <PreCheckLockGate>
        <ReadyStage token={token} onStart={() => setCamMicPassed(true)} proctored={data.proctoring_enabled} />
      </PreCheckLockGate>
    )
  }

  return (
    // Intro is readable without fullscreen (its "I'm ready" CTA enters fullscreen
    // in the same click); verify enforces fullscreen.
    <PreCheckLockGate enforceFullscreen={stage !== 'intro'}>
      <WizardFrame
        companyName={data.company_name}
        jobTitle={data.job_title}
        steps={steps}
        currentIndex={currentIndex}
        accent={appConfig.accent}
      >
        <StageTransition stageKey={stage}>
          {stage === 'intro' && (
            <IntroStage
              token={token}
              companyName={data.company_name}
              jobTitle={data.job_title}
              durationMinutes={data.duration_minutes}
              consentText={data.consent_text}
              proctoringEnabled={data.proctoring_enabled}
            />
          )}
          {stage === 'verify' && <VerifyStage token={token} otpIssuedAt={data.otp_issued_at} />}
        </StageTransition>
      </WizardFrame>
    </PreCheckLockGate>
  )
}
