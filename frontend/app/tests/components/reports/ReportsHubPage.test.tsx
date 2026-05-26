import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '@/tests/_utils/render'

vi.mock('@/lib/auth/tokens', () => ({ getFreshSupabaseToken: vi.fn().mockResolvedValue('tok') }))

let mockSuperAdmin = false
vi.mock('@/lib/hooks/use-me', () => ({ useMe: () => ({ data: { is_super_admin: mockSuperAdmin } }) }))

import ReportsPage from '@/app/(dashboard)/reports/page'

const PAGE = {
  items: [
    { session_id: 's-ready', candidate_id: 'c1', candidate_name: 'Punar', job_title: 'FDE',
      stage_name: 'New Stage', completed_at: '2026-05-24T00:00:00Z',
      report_status: 'ready', verdict: 'reject', overall_score: 36 },
    { session_id: 's-none', candidate_id: 'c2', candidate_name: 'Ishant', job_title: 'CA',
      stage_name: 'Bot Screening', completed_at: '2026-05-23T00:00:00Z',
      report_status: 'none', verdict: null, overall_score: null },
  ],
  total: 2, offset: 0, limit: 50,
}

function stub() {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => PAGE } as Response))
}

afterEach(() => { vi.unstubAllGlobals(); mockSuperAdmin = false })

describe('ReportsPage (hub)', () => {
  it('lists sessions; a ready row links to its report', async () => {
    stub()
    renderWithProviders(<ReportsPage />)
    expect(await screen.findByText('Punar')).toBeInTheDocument()
    expect(screen.getByText('Ishant')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /view report/i })
    expect(link).toHaveAttribute('href', expect.stringContaining('/reports/session/s-ready'))
  })

  it('shows Generate for an ungenerated row only to super-admin', async () => {
    mockSuperAdmin = true
    stub()
    renderWithProviders(<ReportsPage />)
    await screen.findByText('Ishant')
    expect(screen.getByRole('button', { name: /generate/i })).toBeInTheDocument()
  })

  it('hides Generate from non-super-admin', async () => {
    mockSuperAdmin = false
    stub()
    renderWithProviders(<ReportsPage />)
    await screen.findByText('Ishant')
    expect(screen.queryByRole('button', { name: /generate/i })).not.toBeInTheDocument()
  })
})
