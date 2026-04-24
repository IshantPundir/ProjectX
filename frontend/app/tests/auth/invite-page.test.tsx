/** Invite page must call authApi.acceptInvite — never supabase.auth.signUp. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const mockAcceptInvite = vi.fn()
const mockSetSession = vi.fn()
const mockSignUp = vi.fn()
const mockSignInWithPassword = vi.fn()
const mockRouterPush = vi.fn()
const mockRouterRefresh = vi.fn()

vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams({ token: 'raw-token-abc' }),
  useRouter: () => ({ push: mockRouterPush, refresh: mockRouterRefresh }),
}))

vi.mock('@/lib/api/client', () => ({
  apiFetch: vi.fn().mockImplementation(async (path: string) => {
    if (path.startsWith('/api/auth/verify-invite')) {
      return { email: 'user@example.com', client_name: 'TestCo' }
    }
    throw new Error(`Unexpected apiFetch call: ${path}`)
  }),
}))

vi.mock('@/lib/api/auth', () => ({
  authApi: {
    me: vi.fn(),
    acceptInvite: (...args: unknown[]) => mockAcceptInvite(...args),
  },
}))

vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({
    auth: {
      signUp: mockSignUp,
      signInWithPassword: mockSignInWithPassword,
      setSession: mockSetSession,
    },
  }),
}))

import InvitePage from '@/app/(auth)/invite/page'

describe('InvitePage (B3)', () => {
  beforeEach(() => {
    mockAcceptInvite.mockReset()
    mockSetSession.mockReset()
    mockSignUp.mockReset()
    mockSignInWithPassword.mockReset()
    mockRouterPush.mockReset()
    mockRouterRefresh.mockReset()
    mockSetSession.mockResolvedValue({ error: null })
  })

  it('submits to backend and calls setSession with returned tokens', async () => {
    mockAcceptInvite.mockResolvedValueOnce({
      access_token: 'at-xyz',
      refresh_token: 'rt-xyz',
      expires_in: 3600,
      redirect_to: '/onboarding',
    })

    render(<InvitePage />)

    await screen.findByText(/set up your account/i)
    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/set password/i), 'hunter2hunter2')
    await user.type(screen.getByLabelText(/confirm password/i), 'hunter2hunter2')
    await user.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() => {
      expect(mockAcceptInvite).toHaveBeenCalledWith({
        raw_token: 'raw-token-abc',
        password: 'hunter2hunter2',
      })
    })
    expect(mockSetSession).toHaveBeenCalledWith({
      access_token: 'at-xyz',
      refresh_token: 'rt-xyz',
    })
    expect(mockSignUp).not.toHaveBeenCalled()
    expect(mockSignInWithPassword).not.toHaveBeenCalled()
    expect(mockRouterPush).toHaveBeenCalledWith('/onboarding')
  })

  it('rejects open-redirect targets from the backend', async () => {
    mockAcceptInvite.mockResolvedValueOnce({
      access_token: 'at-xyz',
      refresh_token: 'rt-xyz',
      expires_in: 3600,
      redirect_to: 'https://evil.example.com/steal',
    })

    render(<InvitePage />)
    await screen.findByText(/set up your account/i)
    const user = userEvent.setup()
    await user.type(screen.getByLabelText(/set password/i), 'hunter2hunter2')
    await user.type(screen.getByLabelText(/confirm password/i), 'hunter2hunter2')
    await user.click(screen.getByRole('button', { name: /create account/i }))

    await waitFor(() => {
      expect(mockRouterPush).toHaveBeenCalledWith('/')
    })
  })
})
