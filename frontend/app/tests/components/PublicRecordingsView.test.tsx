import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api/reports', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@/lib/api/reports')>()
  return {
    ...mod,
    reportsApi: {
      ...mod.reportsApi,
      getPublicRecordings: vi.fn(async () => ({
        candidate_name: 'Jane Doe',
        job_title: 'FDE',
        stage_label: 'New Stage',
        report: {
          session_id: 's1',
          questions: [],
          reference_photo_url: null,
          verdict: 'advance',
        },
        recording: {
          status: 'ready',
          signed_url: 'https://r2/v',
          offset_ms: 0,
          duration_seconds: 60,
          transcript: [],
        },
        proctoring: { status: 'absent' },
        reel: {
          status: 'absent',
          signed_url: null,
          chapters: [],
          duration_seconds: null,
          eligible: false,
          ineligible_reason: null,
          version: 0,
          generation_error: null,
          expires_at: null,
        },
      })),
    },
  }
})

import { PublicRecordingsView } from '@/components/dashboard/reports/PublicRecordingsView'

function renderView() {
  const qc = new QueryClient()
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
  return render(<PublicRecordingsView token="tok" />, { wrapper })
}

describe('PublicRecordingsView', () => {
  it('renders candidate identity and a watch-session trigger', async () => {
    renderView()
    await waitFor(() => expect(screen.getByText('Jane Doe')).toBeInTheDocument())
    expect(screen.getByText(/FDE/)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /full session/i }),
    ).toBeInTheDocument()
  })

  it('hides the reel trigger when the reel is absent', async () => {
    renderView()
    await waitFor(() => expect(screen.getByText('Jane Doe')).toBeInTheDocument())
    expect(
      screen.queryByRole('button', { name: /highlight reel/i }),
    ).toBeNull()
  })
})
