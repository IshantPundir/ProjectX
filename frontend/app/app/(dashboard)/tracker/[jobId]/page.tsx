'use client'

import { useParams } from 'next/navigation'

import { TrackerKanbanPage } from '@/components/dashboard/tracker/TrackerKanbanPage'

export default function TrackerBoardPage() {
  const params = useParams<{ jobId: string }>()
  return <TrackerKanbanPage jobId={params.jobId} />
}
