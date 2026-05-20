'use client'

import { PROCTORING_END_LABEL, type ProctoringTermination } from '../proctoring/violation-kinds'

export function ProctoringEndedScreen({ reason }: { reason: string | null }) {
  const label =
    reason && reason in PROCTORING_END_LABEL
      ? PROCTORING_END_LABEL[reason as ProctoringTermination]
      : 'a monitoring violation'
  return (
    <div className="px-cine-bg grid min-h-screen place-items-center px-6">
      <div className="px-glass max-w-md rounded-2xl px-8 py-10 text-center">
        <h1 className="font-serif text-2xl text-px-fg">Your interview was ended.</h1>
        <p className="mt-3 text-sm text-px-fg-3">
          Our monitoring detected {label}. This session has ended and cannot be resumed. If you
          believe this was a mistake, contact the hiring team.
        </p>
      </div>
    </div>
  )
}
