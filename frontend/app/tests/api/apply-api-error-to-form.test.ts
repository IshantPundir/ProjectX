import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'

import { ApiError, ApiValidationError } from '@/lib/api/client'
import { applyApiErrorToForm } from '@/lib/api/errors'

const schema = z.object({
  email: z.string().email(),
  password: z.string().min(8),
  profile: z.object({
    about: z.string().min(10),
  }),
})

type FormValues = z.infer<typeof schema>

function renderForm() {
  return renderHook(() => {
    const form = useForm<FormValues>({
      resolver: zodResolver(schema),
      defaultValues: {
        email: '',
        password: '',
        profile: { about: '' },
      },
    })
    // RHF's formState is a Proxy that only tracks keys read during
    // render. Touching `errors` here activates the subscription so
    // that `setError` updates become visible via `formState.errors`
    // after the hook renders (a real component re-renders on form
    // state change and subscribes automatically; this harness does
    // not, so we prime the subscription manually).
    void form.formState.errors
    return form
  }).result
}

/** Apply inside `act()` so RHF's internal React state flushes before we read. */
function applyInAct(
  err: unknown,
  form: ReturnType<typeof renderForm>['current'],
  opts?: Parameters<typeof applyApiErrorToForm<FormValues>>[2],
): boolean {
  let result = false
  act(() => {
    result = applyApiErrorToForm(err, form, opts)
  })
  return result
}

describe('applyApiErrorToForm', () => {
  it('returns false for non-ApiValidationError inputs', () => {
    const form = renderForm().current
    expect(applyInAct(new Error('boom'), form)).toBe(false)
    expect(applyInAct(new ApiError('401', 401), form)).toBe(false)
    expect(applyInAct('string error', form)).toBe(false)
    expect(applyInAct(undefined, form)).toBe(false)
  })

  it('maps a top-level body field', () => {
    const form = renderForm().current
    const err = new ApiValidationError('invalid email', [
      { loc: ['body', 'email'], msg: 'invalid email', type: 'x' },
    ])
    expect(applyInAct(err, form)).toBe(true)
    expect(form.formState.errors.email?.message).toBe('invalid email')
  })

  it('maps multiple fields in one call', () => {
    const form = renderForm().current
    const err = new ApiValidationError('multi', [
      { loc: ['body', 'email'], msg: 'bad email', type: 'x' },
      { loc: ['body', 'password'], msg: 'too short', type: 'y' },
    ])
    expect(applyInAct(err, form)).toBe(true)
    expect(form.formState.errors.email?.message).toBe('bad email')
    expect(form.formState.errors.password?.message).toBe('too short')
  })

  it('maps nested body fields with dotted paths', () => {
    const form = renderForm().current
    const err = new ApiValidationError('nested', [
      { loc: ['body', 'profile', 'about'], msg: 'too short', type: 'x' },
    ])
    expect(applyInAct(err, form)).toBe(true)
    expect(form.formState.errors.profile?.about?.message).toBe('too short')
  })

  it('falls back to fallbackFieldKey when loc does not match a known field', () => {
    const form = renderForm().current
    const err = new ApiValidationError('unknown', [
      { loc: ['body', 'mystery_field'], msg: 'nope', type: 'x' },
    ])
    expect(applyInAct(err, form, { fallbackFieldKey: 'email' })).toBe(true)
    expect(form.formState.errors.email?.message).toBe('nope')
  })

  it('falls back to root error when no fallback provided', () => {
    const form = renderForm().current
    const err = new ApiValidationError('unknown', [
      { loc: ['body', 'mystery_field'], msg: 'nope', type: 'x' },
    ])
    expect(applyInAct(err, form)).toBe(true)
    expect(form.formState.errors.root?.message).toBe('nope')
  })

  it('returns true when at least one field maps (mixed match + miss)', () => {
    const form = renderForm().current
    const err = new ApiValidationError('mixed', [
      { loc: ['body', 'email'], msg: 'bad', type: 'x' },
      { loc: ['body', 'unknown'], msg: 'also bad', type: 'x' },
    ])
    expect(applyInAct(err, form)).toBe(true)
    expect(form.formState.errors.email?.message).toBe('bad')
  })
})
