'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'

import { authApi } from '@/lib/api/auth'
import { ApiError } from '@/lib/api/client'
import { applyApiErrorToForm } from '@/lib/api/errors'
import { createClient } from '@/lib/supabase/client'

import { loginSchema, type LoginFormValues } from './schema'

function EyeIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  )
}

function EyeOffIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
      <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
      <line x1="1" y1="1" x2="23" y2="23" />
    </svg>
  )
}

export default function LoginPage() {
  const router = useRouter()
  const [showPassword, setShowPassword] = useState(false)

  const form = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: '', password: '' },
  })

  async function onSubmit(values: LoginFormValues) {
    try {
      const result = await authApi.login(values)

      const supabase = createClient()
      const { error: sessionError } = await supabase.auth.setSession({
        access_token: result.access_token,
        refresh_token: result.refresh_token,
      })
      if (sessionError) {
        form.setError('root', { message: sessionError.message })
        return
      }

      // Open-redirect guard: allow only same-origin relative paths.
      const safeRedirect =
        result.redirect_to.startsWith('/') &&
        !result.redirect_to.startsWith('//')
          ? result.redirect_to
          : '/'
      router.push(safeRedirect)
      router.refresh()
    } catch (err) {
      if (applyApiErrorToForm(err, form)) return
      if (err instanceof ApiError) {
        form.setError('root', { message: err.message })
        return
      }
      form.setError('root', {
        message: err instanceof Error ? err.message : 'An unexpected error occurred',
      })
    }
  }

  const rootError = form.formState.errors.root?.message

  return (
    <>
      <div className="mb-8 text-center">
        <div
          className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full"
          style={{ background: 'var(--px-accent)' }}
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="white" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <polygon points="5 3 19 12 5 21 5 3" />
          </svg>
        </div>
        <h1
          className="px-serif m-0 text-[32px] font-normal"
          style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
        >
          ProjectX
        </h1>
        <p className="mt-1 text-[13px]" style={{ color: 'var(--px-fg-3)' }}>
          Sign in to your recruiting dashboard
        </p>
      </div>
      <form
        onSubmit={form.handleSubmit(onSubmit)}
        className="space-y-4 rounded-[12px] border p-7"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
          boxShadow: 'var(--px-shadow-sm)',
        }}
      >
        {rootError && (
          <p
            className="rounded-md border p-3 text-[13px]"
            style={{
              color: 'var(--px-danger)',
              background: 'var(--px-danger-bg)',
              borderColor: 'var(--px-danger-line)',
            }}
          >
            {rootError}
          </p>
        )}
        <div>
          <label htmlFor="login-email" className="px-label">Email</label>
          <input
            id="login-email"
            type="email"
            autoComplete="email"
            className="px-input"
            placeholder="you@company.com"
            {...form.register('email')}
          />
          {form.formState.errors.email && (
            <p className="px-hint" style={{ color: 'var(--px-danger)' }}>
              {form.formState.errors.email.message}
            </p>
          )}
        </div>
        <div>
          <label htmlFor="login-password" className="px-label">Password</label>
          <div className="relative">
            <input
              id="login-password"
              type={showPassword ? 'text' : 'password'}
              autoComplete="current-password"
              className="px-input pr-10"
              {...form.register('password')}
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              className="absolute inset-y-0 right-0 flex cursor-pointer items-center px-3"
              style={{ color: 'var(--px-fg-4)' }}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
            >
              {showPassword ? <EyeOffIcon /> : <EyeIcon />}
            </button>
          </div>
          {form.formState.errors.password && (
            <p className="px-hint" style={{ color: 'var(--px-danger)' }}>
              {form.formState.errors.password.message}
            </p>
          )}
        </div>
        <button
          type="submit"
          disabled={form.formState.isSubmitting}
          className="px-btn primary lg w-full"
        >
          {form.formState.isSubmitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
      <p className="mt-4 text-center text-[12.5px]" style={{ color: 'var(--px-fg-4)' }}>
        Don&apos;t have an account? Contact your{' '}
        <strong className="font-semibold" style={{ color: 'var(--px-fg-3)' }}>
          Company Admin
        </strong>{' '}
        for an invite.
      </p>
    </>
  )
}
