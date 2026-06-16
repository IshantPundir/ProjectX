// app/interview/[token]/IntroStage.tsx
'use client'

import dynamic from 'next/dynamic'
import { toast } from 'sonner'

import { Button } from '@/components/px'
import { requestAppFullscreen } from '@/hooks/use-fullscreen-lock'
import { useConsent } from '@/lib/hooks/use-consent'
import { ConsentDialog } from './ConsentDialog'
import {
  ArjunGlyph,
  QuietRoomGlyph,
  SingleScreenGlyph,
  ShieldGlyph,
  OneTimeLinkGlyph,
} from './illustrations/glyphs'
import { InstructionList, type Instruction } from './InstructionList'

// The hero is the same Aura orb the candidate meets in the live interview, so
// the intro previews the real session. Lazy + ssr:false keeps the heavy WebGL
// shader out of the light pre-check bundle; Aura renders a static gradient orb
// under prefers-reduced-motion (no WebGL). One instance only — never two
// simultaneous WebGL contexts.
const HeroAura = dynamic(() => import('@/components/agents-ui/aura').then((m) => m.Aura), {
  ssr: false,
  loading: () => <span aria-hidden className="aura-mark block size-[224px]" />,
})

interface Props {
  token: string
  companyName: string
  jobTitle: string
  durationMinutes: number
  consentText: string
  proctoringEnabled: boolean
}

function buildInstructions(proctoringEnabled: boolean): Instruction[] {
  const items: Instruction[] = [
    {
      id: 'arjun',
      Icon: ArjunGlyph,
      title: 'Meet Arjun, your AI interviewer',
      detail:
        'Your screening is led by Arjun, a friendly AI. Just talk naturally — there are no trick questions.',
    },
    {
      id: 'quiet',
      Icon: QuietRoomGlyph,
      title: 'Find a quiet spot, alone',
      detail:
        'Pick a calm room with no one else around. Background noise and other voices can disrupt the conversation.',
    },
    {
      id: 'screen',
      Icon: SingleScreenGlyph,
      title: 'Use a single screen',
      detail:
        "Extra monitors aren't allowed — please disconnect them or use just one screen before you start.",
    },
  ]
  if (proctoringEnabled) {
    items.push({
      id: 'shield',
      Icon: ShieldGlyph,
      title: 'This is a proctored screening',
      detail:
        "Your camera and focus are monitored to keep things fair. It's for review only — never an automatic rejection.",
    })
  }
  items.push({
    id: 'link',
    Icon: OneTimeLinkGlyph,
    title: 'One-time link — use it when you’re ready',
    detail:
      "Not ready yet? You can revisit this page anytime. But once you click Start, this link is spent — you'll need a new invite from the recruiter to come back.",
    tone: 'caution',
  })
  return items
}

/**
 * Stage 1 — welcome + instructions + consent soft-fold. Clicking "I'm ready"
 * records the AIVIA consent event (POST /consent via useConsent). On success the
 * cached pre-check state flips to 'consented' and WizardShell advances; on error
 * the CTA re-enables and a toast explains. The full consent text is always one
 * tap away via ConsentDialog.
 */
export function IntroStage({
  token,
  companyName,
  jobTitle,
  durationMinutes,
  consentText,
  proctoringEnabled,
}: Props) {
  const consent = useConsent(token)
  const instructions = buildInstructions(proctoringEnabled)

  const onReady = () => {
    // Use this click's user-activation to enter fullscreen (browsers require a
    // gesture), then record consent — both fire on the single "I'm ready" tap.
    requestAppFullscreen()
    consent.mutate(
      { consented: true, user_agent: navigator.userAgent },
      { onError: (err) => toast.error(err.message) },
    )
  }

  return (
    <div className="grid items-center gap-8 lg:grid-cols-[0.85fr_1.15fr] lg:gap-12">
      {/* Hero — the live-session Aura orb (single WebGL instance). On mobile the
          grid collapses and the orb sits above the content. Scaled up a touch
          for presence (more on desktop). */}
      <div className="flex justify-center">
        <div className="scale-110 lg:scale-125">
          <HeroAura state="listening" size="lg" />
        </div>
      </div>

      {/* Content side */}
      <section className="flex flex-col">
        <p className="text-[11px] font-semibold uppercase tracking-[1.2px] text-px-fg-4">
          {companyName} &middot; Screening
        </p>
        <h1 className="px-serif mt-1.5 text-[clamp(28px,6vw,40px)] font-normal leading-[1.08] tracking-[-0.5px] text-px-fg">
          {jobTitle}
        </h1>
        <div className="mt-3 inline-flex w-fit items-center gap-2 rounded-full border border-px-hairline bg-px-surface/60 px-3 py-1 text-[12.5px] font-medium text-px-fg-2">
          <span className="size-1.5 rounded-full bg-px-accent" aria-hidden />
          AI screening with Arjun &middot; ~{durationMinutes} min
        </div>

        <div className="mt-6">
          <InstructionList items={instructions} />
        </div>

        <div className="mt-7 flex flex-col gap-3">
          <Button
            size="lg"
            onClick={onReady}
            disabled={consent.isPending}
            aria-busy={consent.isPending}
            className="w-full sm:w-auto"
          >
            {consent.isPending ? 'Getting ready…' : (<>I&apos;m ready <span aria-hidden>&#x2192;</span></>)}
          </Button>
          <p className="text-[12.5px] leading-relaxed text-px-fg-3">
            By starting, you consent to this AI-led interview and its recording.{' '}
            <ConsentDialog consentText={consentText} />
          </p>
        </div>
      </section>
    </div>
  )
}
