'use client'

import { createContext, useContext } from 'react'

import type { ProctoringAnalysis, RecordingPlayback } from '@/lib/api/reports'

/**
 * Pre-fetched playback data for the PUBLIC /recordings/<token> page. When this
 * context is present, the recording/proctoring hooks return it directly instead
 * of calling the authenticated /api/reports endpoints (which require a Supabase
 * token the external recruiter does not have). Absent in the recruiter app →
 * hooks behave normally.
 */
export interface PublicPlaybackData {
  recording: RecordingPlayback
  proctoring: ProctoringAnalysis
}

const PublicPlaybackContext = createContext<PublicPlaybackData | null>(null)

export function PublicPlaybackProvider({
  value,
  children,
}: {
  value: PublicPlaybackData
  children: React.ReactNode
}) {
  return (
    <PublicPlaybackContext.Provider value={value}>
      {children}
    </PublicPlaybackContext.Provider>
  )
}

export function usePublicPlayback(): PublicPlaybackData | null {
  return useContext(PublicPlaybackContext)
}
