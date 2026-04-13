'use client'

import type { BankWithQuestionsResponse } from '@/lib/api/question-banks'

type Props = {
  bank: BankWithQuestionsResponse
  onConfirm: () => void
  onCancel: () => void
}

// Placeholder stub — real implementation lands in P15.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function ConfirmBankDialog(_: Props) {
  return null
}
