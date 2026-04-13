'use client'

import type { BankWithQuestionsResponse } from '@/lib/api/question-banks'

type Props = {
  jobId: string
  stageId: string
  bank: BankWithQuestionsResponse
}

// Placeholder stub — real implementation lands in P15.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function QuestionList(_: Props) {
  return null
}
