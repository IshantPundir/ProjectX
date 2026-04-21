'use client'

import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
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
      <div className="rounded-lg border border-zinc-200 bg-white p-6">
        <h2 className="text-lg font-semibold">Consent to interview</h2>
        <p className="mt-3 text-sm leading-relaxed text-zinc-700 whitespace-pre-wrap">
          {consentText}
        </p>
      </div>
      <label className="flex items-start gap-3 text-sm text-zinc-700">
        <input
          type="checkbox"
          className="mt-0.5 h-4 w-4"
          checked={checked}
          onChange={(e) => setChecked(e.target.checked)}
        />
        I have read and understood the above. I consent to proceeding with this
        interview.
      </label>
      <Button disabled={!checked || consent.isPending} onClick={onContinue}>
        {consent.isPending ? 'Saving…' : 'Continue'}
      </Button>
    </section>
  )
}
