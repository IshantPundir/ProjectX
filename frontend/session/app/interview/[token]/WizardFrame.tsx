// app/interview/[token]/WizardFrame.tsx
'use client'

import type { ReactNode } from 'react'

import { BrandMark } from '@/components/interview/BrandMark'
import { StageProgress } from './StageProgress'

interface WizardFrameProps {
  companyName: string
  jobTitle: string
  steps: string[]
  currentIndex: number
  accent?: string
  children: ReactNode
}

function Header({ companyName, jobTitle }: { companyName: string; jobTitle: string }) {
  return (
    <div className="flex items-center gap-2.5">
      <BrandMark variant="mark" className="h-7 w-7" />
      <span className="text-[13px] text-px-fg">
        <b className="font-semibold">{companyName}</b>
        {jobTitle && <span className="text-px-fg-4"> · {jobTitle}</span>}
      </span>
    </div>
  )
}

/**
 * Outer chrome for the pre-check stages: BinQle header + screening identity, a
 * minimal progress indicator, and the active stage (already wrapped in
 * StageTransition by WizardShell). The site-wide AnimatedBackground shows through
 * the transparent .px-cine-bg.
 */
export function WizardFrame({
  companyName,
  jobTitle,
  steps,
  currentIndex,
  accent,
  children,
}: WizardFrameProps) {
  return (
    <div
      className="px-cine-bg flex min-h-dvh flex-col"
      style={accent ? ({ ['--px-accent' as string]: accent } as React.CSSProperties) : undefined}
    >
      <header className="mx-auto flex w-full max-w-5xl items-center justify-between px-6 pt-6">
        <Header companyName={companyName} jobTitle={jobTitle} />
        <StageProgress steps={steps} currentIndex={currentIndex} className="hidden sm:flex" />
      </header>

      <main className="mx-auto flex w-full max-w-5xl flex-1 items-center px-6 py-8">
        <div className="w-full">{children}</div>
      </main>
    </div>
  )
}
