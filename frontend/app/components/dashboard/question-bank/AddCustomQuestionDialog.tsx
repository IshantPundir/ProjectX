'use client'

import type { BankWithQuestionsResponse } from '@/lib/api/question-banks'

type Props = {
  jobId: string
  stageId: string
  bank: BankWithQuestionsResponse
  onClose: () => void
}

// Placeholder stub — real implementation lands in P15.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function AddCustomQuestionDialog(_: Props) {
  return null
}
