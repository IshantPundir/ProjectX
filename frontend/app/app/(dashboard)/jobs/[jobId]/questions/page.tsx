'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
import { ArrowLeft } from 'lucide-react'
import { QuestionsReviewContent } from '@/components/dashboard/question-bank/QuestionsReviewContent'

export default function QuestionsReviewPage() {
  const params = useParams<{ jobId: string }>()
  const jobId = params.jobId

  return (
    <div>
      <div className="mb-4">
        <Link
          href={`/jobs/${jobId}/pipeline`}
          className="text-sm text-zinc-500 hover:text-zinc-900 inline-flex items-center gap-1"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Back to pipeline
        </Link>
        <h1 className="text-2xl font-semibold text-zinc-900 mt-2">Interview Questions</h1>
        <p className="text-sm text-zinc-500 mt-1">
          Review, edit, and confirm the question bank for each pipeline stage. Confirmed banks are ready for Phase 3 interview sessions.
        </p>
      </div>

      <div className="bg-white rounded-xl border border-zinc-200 overflow-hidden">
        <QuestionsReviewContent />
      </div>
    </div>
  )
}
