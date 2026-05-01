'use client'

import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/px'
import { useConsent } from '@/lib/hooks/use-consent'

interface Props {
  token: string
  consentText: string
}

export function ConsentStep({ token, consentText }: Props) {
  const [checked, setChecked] = useState(false)
  const consent = useConsent(token)

  const onContinue = () => {
    consent.mutate(
      { consented: true, user_agent: navigator.userAgent },
      {
        onError: (err) => toast.error(err.message),
      },
    )
  }

  return (
    <section className="space-y-6">
      <div
        className="rounded-[12px] border p-6"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
        }}
      >
        <div
          className="mb-2 text-[10.5px] font-semibold uppercase"
          style={{ letterSpacing: '1.1px', color: 'var(--px-fg-4)' }}
        >
          Consent
        </div>
        <p
          className="text-[14px] whitespace-pre-wrap"
          style={{ color: 'var(--px-fg-2)', lineHeight: 1.7 }}
        >
          {consentText}
        </p>
      </div>
      <label
        className="flex items-start gap-3 text-[13.5px]"
        style={{ color: 'var(--px-fg-2)' }}
      >
        <input
          type="checkbox"
          className="px-check mt-0.5"
          checked={checked}
          onChange={(e) => setChecked(e.target.checked)}
        />
        I have read and understood the above. I consent to proceeding with this
        interview.
      </label>
      <Button
        size="lg"
        disabled={!checked || consent.isPending}
        onClick={onContinue}
      >
        {consent.isPending ? 'Saving…' : 'Continue →'}
      </Button>
    </section>
  )
}
