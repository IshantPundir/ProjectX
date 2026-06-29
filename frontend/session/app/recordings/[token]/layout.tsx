'use client'

import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import '../recordings-theme.css'

/**
 * Public recordings route — its own minimal QueryClient boundary.
 * Wraps children in a daylight-theme container so the vendored
 * report components render with the same token set as the recruiter app.
 */
export default function RecordingsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const [qc] = useState(() => new QueryClient())
  return (
    <QueryClientProvider client={qc}>
      <div data-px-theme="daylight" className="recordings-root min-h-dvh">
        {children}
      </div>
    </QueryClientProvider>
  )
}
