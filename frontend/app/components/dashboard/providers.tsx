'use client'

import {
  MutationCache,
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from '@tanstack/react-query'
import { ReactQueryDevtools } from '@tanstack/react-query-devtools'
import { useRouter } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'

import { Toaster } from '@/components/px'
import { handleAuthError, type AppRouter } from '@/lib/auth/handle-error'

export function DashboardProviders({ children }: { children: React.ReactNode }) {
  const router = useRouter()

  // Stable callback ref: keeps a reference to the latest router so the
  // QueryCache/MutationCache onError callbacks always navigate with the
  // current router without requiring a QueryClient re-creation.
  const onErrorRef = useRef<(err: unknown) => void>(() => undefined)
  useEffect(() => {
    onErrorRef.current = (err: unknown) => {
      void handleAuthError(err, router as AppRouter)
    }
  }, [router])

  /* eslint-disable react-hooks/refs -- onErrorRef.current is accessed only
     inside onError, which fires in event-handler context, never during render.
     The rule cannot statically prove deferral through the useState initializer. */
  const [queryClient] = useState(() => {
    const onError = (err: unknown) => {
      onErrorRef.current(err)
    }
    /* eslint-enable react-hooks/refs */
    return new QueryClient({
      queryCache: new QueryCache({ onError }),
      mutationCache: new MutationCache({ onError }),
      defaultOptions: {
        queries: {
          staleTime: 10_000,
          refetchOnWindowFocus: false,
        },
      },
    })
  })

  return (
    <QueryClientProvider client={queryClient}>
      {children}
      <Toaster />
      {process.env.NODE_ENV === 'development' && <ReactQueryDevtools />}
    </QueryClientProvider>
  )
}
