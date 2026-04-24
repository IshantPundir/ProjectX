import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import LoginPage from '@/app/(auth)/login/page'

const pushMock = vi.fn()
const refreshMock = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock, refresh: refreshMock }),
}))

const setSessionMock = vi.fn(async () => ({ error: null }))
vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({
    auth: {
      setSession: setSessionMock,
      signInWithPassword: vi.fn(() => {
        throw new Error('signInWithPassword should never be called from the login page in B4')
      }),
    },
  }),
}))

const loginMock = vi.fn()
vi.mock('@/lib/api/auth', () => ({
  authApi: { login: (body: unknown) => loginMock(body) },
}))

describe('LoginPage (B4)', () => {
  beforeEach(() => {
    pushMock.mockClear()
    refreshMock.mockClear()
    setSessionMock.mockClear()
    loginMock.mockReset()
  })

  it('submits credentials to authApi.login and installs the session', async () => {
    loginMock.mockResolvedValue({
      access_token: 'a.b.c',
      refresh_token: 'refresh',
      expires_in: 3600,
      redirect_to: '/',
    })

    render(<LoginPage />)
    await userEvent.type(screen.getByLabelText(/email/i), 'user@example.com')
    await userEvent.type(screen.getByLabelText(/^password$/i), 'hunter2hunter2')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(loginMock).toHaveBeenCalledWith({
        email: 'user@example.com',
        password: 'hunter2hunter2',
      })
    })
    expect(setSessionMock).toHaveBeenCalledWith({
      access_token: 'a.b.c',
      refresh_token: 'refresh',
    })
    expect(pushMock).toHaveBeenCalledWith('/')
  })

  it('maps 401 to a form-level error without redirecting', async () => {
    const { ApiError } = await import('@/lib/api/client')
    loginMock.mockRejectedValue(
      new ApiError('Invalid email or password.', 401),
    )

    render(<LoginPage />)
    await userEvent.type(screen.getByLabelText(/email/i), 'bad@example.com')
    await userEvent.type(screen.getByLabelText(/^password$/i), 'nope')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(
        screen.getByText(/invalid email or password/i),
      ).toBeInTheDocument()
    })
    expect(pushMock).not.toHaveBeenCalled()
    expect(setSessionMock).not.toHaveBeenCalled()
  })

  it('does NOT reference supabase.auth.signInWithPassword', async () => {
    loginMock.mockResolvedValue({
      access_token: 'a',
      refresh_token: 'r',
      expires_in: 10,
      redirect_to: '/',
    })

    render(<LoginPage />)
    await userEvent.type(screen.getByLabelText(/email/i), 'u@x.com')
    await userEvent.type(screen.getByLabelText(/^password$/i), 'hunter2hunter2')
    await userEvent.click(screen.getByRole('button', { name: /sign in/i }))
    await waitFor(() => expect(loginMock).toHaveBeenCalled())
  })
})
