'use client'

import { AlertCircle, Check, Clock, Lock, Loader2 } from 'lucide-react'
import type { BankStatus } from '@/lib/api/question-banks'

type Props = {
  status: BankStatus
  small?: boolean
}

const STATUS_STYLES: Record<BankStatus, { bg: string; text: string; label: string }> = {
  draft: { bg: 'bg-zinc-100', text: 'text-zinc-600', label: 'DRAFT' },
  generating: { bg: 'bg-blue-50', text: 'text-blue-700', label: 'GENERATING' },
  reviewing: { bg: 'bg-amber-50', text: 'text-amber-700', label: 'REVIEWING' },
  confirmed: { bg: 'bg-emerald-50', text: 'text-emerald-700', label: 'CONFIRMED' },
  failed: { bg: 'bg-red-50', text: 'text-red-700', label: 'FAILED' },
}

export function BankStatusBadge({ status, small }: Props) {
  const style = STATUS_STYLES[status]
  const sizeClass = small ? 'text-[9px] px-1.5 py-0.5' : 'text-[10px] px-2 py-1'

  const Icon =
    status === 'generating' ? Loader2 :
    status === 'confirmed' ? Lock :
    status === 'failed' ? AlertCircle :
    status === 'reviewing' ? Clock :
    Check

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full font-semibold ${sizeClass} ${style.bg} ${style.text}`}
    >
      <Icon
        className={`${small ? 'w-2.5 h-2.5' : 'w-3 h-3'} ${status === 'generating' ? 'animate-spin' : ''}`}
        aria-hidden="true"
      />
      {style.label}
    </span>
  )
}
