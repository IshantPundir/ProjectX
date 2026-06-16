'use client'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'

/**
 * Public recordings route — its own minimal QueryClient boundary. This route
 * lives OUTSIDE (dashboard), so it does not inherit DashboardProviders (which
 * also carries Supabase-authenticated concerns the external viewer must not hit).
 */
export default function RecordingsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const [queryClient] = useState(() => new QueryClient())
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}
