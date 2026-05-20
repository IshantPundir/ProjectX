'use client'

import { Button } from '@/components/px'

export function WelcomeStep({
  durationMinutes,
  onBegin,
}: {
  durationMinutes: number
  onBegin: () => void
}) {
  return (
    <section
      className="rounded-[14px] border p-6"
      style={{ background: 'var(--px-surface)', borderColor: 'var(--px-hairline)' }}
    >
      <div
        className="mb-2 text-[10.5px] font-semibold uppercase"
        style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
      >
        Welcome
      </div>
      <h2
        className="px-serif m-0 mb-3 text-[26px] font-normal"
        style={{ letterSpacing: '-0.5px', color: 'var(--px-fg)' }}
      >
        A calm, conversational interview
      </h2>
      <ul className="mb-6 space-y-2 text-[14px]" style={{ color: 'var(--px-fg-2)', lineHeight: 1.6 }}>
        <li>• Speak naturally — it&apos;s a conversation, with no trick questions.</li>
        <li>• Take your time. You can pause to think before answering.</li>
        <li>• It takes about {durationMinutes} minutes. You&apos;ll see your progress as you go.</li>
      </ul>
      <Button size="lg" onClick={onBegin}>
        Begin →
      </Button>
    </section>
  )
}
