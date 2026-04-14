'use client'

import { useEffect } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { useBanksOverview } from '@/lib/hooks/use-banks-overview'

export default function QuestionsRedirectPage() {
  const router = useRouter()
  const params = useParams<{ jobId: string }>()
  const jobId = params.jobId
  const { data: overview, isLoading } = useBanksOverview(jobId)

  useEffect(() => {
    if (isLoading) return
    const firstBank = overview?.banks[0]
    const target = firstBank
      ? `/jobs/${jobId}/pipeline?stage=${firstBank.stage_id}`
      : `/jobs/${jobId}/pipeline`
    router.replace(target)
  }, [isLoading, overview, jobId, router])

  return (
    <div className="flex items-center justify-center h-64 text-sm text-zinc-500">
      Redirecting to pipeline…
    </div>
  )
}
