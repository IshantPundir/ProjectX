import type { ReactNode } from 'react'

export default function InterviewLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-900">
      <div className="max-w-2xl mx-auto px-4 py-12">{children}</div>
    </div>
  )
}
