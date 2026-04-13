'use client'

import { useState } from 'react'
import { useBankWithQuestions } from '@/lib/hooks/use-bank-with-questions'
import { useGenerateStageQuestions } from '@/lib/hooks/use-generate-questions'
import { useConfirmBank } from '@/lib/hooks/use-confirm-bank'
import { BankHeader } from './BankHeader'
import { QuestionList } from './QuestionList'
import { AddCustomQuestionDialog } from './AddCustomQuestionDialog'
import { ConfirmBankDialog } from './ConfirmBankDialog'

type Props = {
  jobId: string
  stageId: string
}

export function QuestionsMainPane({ jobId, stageId }: Props) {
  const { data: bank, isLoading } = useBankWithQuestions(jobId, stageId)
  const generateMutation = useGenerateStageQuestions(jobId, stageId)
  const confirmMutation = useConfirmBank(jobId, stageId)

  const [addDialogOpen, setAddDialogOpen] = useState(false)
  const [confirmDialogOpen, setConfirmDialogOpen] = useState(false)

  if (isLoading || !bank) {
    return <div className="p-8 text-sm text-zinc-500">Loading bank…</div>
  }

  return (
    <div className="p-6">
      <BankHeader
        bank={bank}
        isSaving={generateMutation.isPending}
        saveFailed={generateMutation.isError}
        onGenerate={() => generateMutation.mutate()}
        onRegenerate={() => generateMutation.mutate()}
        onConfirm={() => setConfirmDialogOpen(true)}
        onAddCustom={() => setAddDialogOpen(true)}
      />
      <div className="mt-6">
        <QuestionList
          jobId={jobId}
          stageId={stageId}
          bank={bank}
        />
      </div>

      {addDialogOpen && (
        <AddCustomQuestionDialog
          jobId={jobId}
          stageId={stageId}
          bank={bank}
          onClose={() => setAddDialogOpen(false)}
        />
      )}

      {confirmDialogOpen && (
        <ConfirmBankDialog
          bank={bank}
          onConfirm={() => {
            confirmMutation.mutate()
            setConfirmDialogOpen(false)
          }}
          onCancel={() => setConfirmDialogOpen(false)}
        />
      )}
    </div>
  )
}
