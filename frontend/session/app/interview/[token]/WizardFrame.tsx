'use client'

import type { ReactNode } from 'react'

import dynamic from 'next/dynamic'
import { WizardStepper, type WizardStepKey } from './WizardStepper'

// Lazy so the WebGL aura shader stays out of the light pre-join bundle.
const Aura = dynamic(() => import('@/components/agents-ui/aura').then((m) => m.Aura), {
  ssr: false,
  loading: () => <span aria-hidden className="aura-mark block size-[224px] self-start" />,
})

interface WizardFrameProps {
  companyName: string
  jobTitle: string
  current: WizardStepKey | 'welcome'
  otpRequired: boolean
  accent?: string
  children: ReactNode
}

function Brand({ companyName, jobTitle }: { companyName: string; jobTitle: string }) {
  return (
    <div className="flex items-center gap-2 text-[13px] text-px-fg">
      <span className="grid size-6 place-items-center rounded-[6px] bg-px-accent text-[11px] font-bold text-white">
        {(companyName || 'P').slice(0, 1).toUpperCase()}
      </span>
      <span>
        <b className="font-semibold">{companyName || 'ProjectX'}</b>
        {jobTitle && <span className="text-px-fg-4"> · {jobTitle}</span>}
      </span>
    </div>
  )
}

export function WizardFrame({
  companyName,
  jobTitle,
  current,
  otpRequired,
  accent,
  children,
}: WizardFrameProps) {
  return (
    <div
      className="px-cine-bg min-h-screen"
      style={accent ? ({ ['--px-accent' as string]: accent } as React.CSSProperties) : undefined}
    >
      <div className="grid min-h-screen lg:grid-cols-2">
        {/* Left pane — reassurance (desktop only) */}
        <aside className="relative hidden flex-col justify-center gap-7 px-12 py-10 lg:flex">
          <Brand companyName={companyName} jobTitle={jobTitle} />
          <Aura state="listening" audioTrack={undefined} size="lg" className="self-start" />
          <div>
            <h1 className="font-serif text-[34px] font-medium leading-[1.1] text-px-fg">
              Meet your<br />interviewer
            </h1>
            <p className="mt-3 max-w-[300px] text-[13px] leading-relaxed text-px-fg-3">
              A calm, conversational AI screen. Take your time — there are no trick questions.
            </p>
          </div>
          <WizardStepper current={current} otpRequired={otpRequired} />
        </aside>

        {/* Right pane — the task */}
        <main className="flex items-center justify-center px-5 py-10 sm:px-8">
          <div className="w-full max-w-md">
            {/* Mobile header (left-pane condensed) */}
            <div className="mb-6 flex flex-col gap-4 lg:hidden">
              <Brand companyName={companyName} jobTitle={jobTitle} />
              <WizardStepper current={current} otpRequired={otpRequired} />
            </div>
            {children}
          </div>
        </main>
      </div>
    </div>
  )
}
