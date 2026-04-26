import { describe, expect, it, vi } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'

import { renderWithProviders } from '../_utils/render'
import { OrgUnitInlineCreate } from '@/components/dashboard/org-units/OrgUnitInlineCreate'

function defaultProps(overrides: object = {}) {
  return {
    unitType: 'region' as const,
    x: 100,
    y: 100,
    onSubmit: vi.fn(),
    onCancel: vi.fn(),
    pending: false,
    error: null as string | null,
    ...overrides,
  }
}

describe('OrgUnitInlineCreate', () => {
  it('renders the type chip and an autofocused name input', () => {
    renderWithProviders(<OrgUnitInlineCreate {...defaultProps()} />)
    expect(screen.getByText(/\+ Region/i)).toBeInTheDocument()
    const input = screen.getByPlaceholderText(/Name the new region/i)
    expect(input).toBeInTheDocument()
    expect(input).toHaveFocus()
  })

  it('calls onSubmit with the trimmed name on Enter', () => {
    const onSubmit = vi.fn()
    renderWithProviders(<OrgUnitInlineCreate {...defaultProps({ onSubmit })} />)
    const input = screen.getByPlaceholderText(/Name the new region/i)
    fireEvent.change(input, { target: { value: '  Engineering  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit).toHaveBeenCalledWith('Engineering')
  })

  it('does not submit on Enter when the input is empty or whitespace-only', () => {
    const onSubmit = vi.fn()
    renderWithProviders(<OrgUnitInlineCreate {...defaultProps({ onSubmit })} />)
    const input = screen.getByPlaceholderText(/Name the new region/i)
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.change(input, { target: { value: '   ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('calls onCancel on Escape', () => {
    const onCancel = vi.fn()
    renderWithProviders(<OrgUnitInlineCreate {...defaultProps({ onCancel })} />)
    const input = screen.getByPlaceholderText(/Name the new region/i)
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('shows an error message when error prop is set', () => {
    renderWithProviders(
      <OrgUnitInlineCreate {...defaultProps({ error: 'Name already taken' })} />,
    )
    expect(screen.getByText(/Name already taken/)).toBeInTheDocument()
  })

  it('disables the input while pending', () => {
    renderWithProviders(<OrgUnitInlineCreate {...defaultProps({ pending: true })} />)
    expect(screen.getByPlaceholderText(/Name the new region/i)).toBeDisabled()
  })
})
