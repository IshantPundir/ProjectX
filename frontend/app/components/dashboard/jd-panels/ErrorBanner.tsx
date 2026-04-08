'use client'

import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { jobsApi } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

type Props = {
  jobId: string
  error: string | null
}

export function ErrorBanner({ jobId, error }: Props) {
  const [retrying, setRetrying] = useState(false)
  const queryClient = useQueryClient()

  async function handleRetry() {
    setRetrying(true)
    try {
      const token = await getFreshSupabaseToken()
      await jobsApi.retry(token, jobId)
      queryClient.invalidateQueries({ queryKey: ['jobs', jobId] })
      toast.success('Retry dispatched')
    } catch (e) {
      toast.error(`Retry failed: ${(e as Error).message}`)
    } finally {
      setRetrying(false)
    }
  }

  return (
    <div className="bg-red-50 border border-red-200 rounded-lg p-5 mb-4">
      <div className="flex items-start gap-3">
        <div className="text-red-500 text-lg leading-none mt-0.5" aria-hidden>
          !
        </div>
        <div className="flex-1">
          <h3 className="text-sm font-semibold text-red-900 mb-1">
            Extraction failed
          </h3>
          <p className="text-sm text-red-700 mb-3">
            {error || 'An unknown error occurred. Please retry.'}
          </p>
          <Button
            onClick={handleRetry}
            disabled={retrying}
            variant="outline"
            size="sm"
          >
            {retrying ? 'Retrying…' : 'Retry extraction'}
          </Button>
        </div>
      </div>
    </div>
  )
}
