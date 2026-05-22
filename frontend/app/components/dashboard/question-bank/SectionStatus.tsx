'use client'

import { AlertCircle, CheckCircle2, Loader2, MinusCircle } from 'lucide-react'

type Props = {
  /** Raw value from `bank.generation_status_by_kind[phase]`. May be undefined. */
  status: string | undefined
}

type StatusStyle = {
  bg: string
  text: string
  label: string
  icon: React.ElementType
  spin?: boolean
}

const STATUS_MAP: Record<string, StatusStyle> = {
  generating: {
    bg: 'bg-blue-50',
    text: 'text-blue-700',
    label: 'Generating…',
    icon: Loader2,
    spin: true,
  },
  reviewing: {
    bg: 'bg-emerald-50',
    text: 'text-emerald-700',
    label: 'Ready',
    icon: CheckCircle2,
  },
  confirmed: {
    bg: 'bg-emerald-50',
    text: 'text-emerald-700',
    label: 'Ready',
    icon: CheckCircle2,
  },
  failed: {
    bg: 'bg-red-50',
    text: 'text-red-700',
    label: 'Failed',
    icon: AlertCircle,
  },
  skipped_no_eligible_signals: {
    bg: 'bg-zinc-100',
    text: 'text-zinc-500',
    label: 'None applicable',
    icon: MinusCircle,
  },
}

const FALLBACK: StatusStyle = {
  bg: 'bg-zinc-100',
  text: 'text-zinc-500',
  label: 'Pending',
  icon: MinusCircle,
}

/**
 * A small pill that reflects the per-phase generation status for a question
 * bank section (Behavioral / Technical). Mirrors the visual style of
 * BankStatusBadge using the same px design tokens and Tailwind classes.
 */
function resolveStyle(status: string | undefined): StatusStyle {
  if (status !== undefined && status in STATUS_MAP) {
    return STATUS_MAP[status]
  }
  return FALLBACK
}

export function SectionStatus({ status }: Props) {
  const style = resolveStyle(status)
  const { icon: Icon, bg, text, label, spin } = style

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full text-[9px] font-semibold px-1.5 py-0.5 ${bg} ${text}`}
    >
      <Icon
        className={`w-2.5 h-2.5 ${spin ? 'animate-spin' : ''}`}
        aria-hidden="true"
      />
      {label}
    </span>
  )
}
