import type { SignalItem } from '@/lib/api/jobs'

export function suggestQuestions(s: SignalItem): string[] {
  // Non-LLM fallback — three generic probes keyed off the signal type. Keeps
  // the inspector useful until per-signal question drafting ships.
  const v = s.value
  if (s.type === 'competency') {
    return [
      `Walk me through a time you owned ${v} end-to-end.`,
      `What's a decision you regret around ${v}?`,
      `How do you know when you've gone deep enough on ${v}?`,
    ]
  }
  if (s.type === 'experience') {
    return [
      `Tell me about your ${v} in the most technically demanding role you've held.`,
      `What patterns repeat across ${v} that most people miss?`,
      `Where did your mental model for ${v} break, and what replaced it?`,
    ]
  }
  if (s.type === 'credential') {
    return [
      `How does your ${v} actually show up in day-to-day work?`,
      `What's something your ${v} didn't prepare you for?`,
    ]
  }
  return [
    `Tell me a story about ${v} under real stakes.`,
    `What would we see on Day 1 that tells us you have ${v}?`,
  ]
}
