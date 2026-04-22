import type { ReactNode } from 'react'

import { InterviewProviders } from '@/components/interview/providers'

export default function InterviewLayout({ children }: { children: ReactNode }) {
  return (
    <InterviewProviders>
      <div
        className="min-h-screen w-full"
        style={{
          background: 'var(--px-bg)',
          color: 'var(--px-fg)',
        }}
      >
        {children}
      </div>
    </InterviewProviders>
  )
}
