import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { DangerConfirmDialog } from '@/components/px'

describe('DangerConfirmDialog', () => {
  it('renders title, description, and labelled buttons when open', () => {
    render(
      <DangerConfirmDialog
        open
        title="Delete item"
        description="Are you sure?"
        confirmLabel="Delete"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText('Delete item')).toBeInTheDocument()
    expect(screen.getByText('Are you sure?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument()
  })

  it('does not render anything when closed', () => {
    render(
      <DangerConfirmDialog
        open={false}
        title="Delete item"
        description="Are you sure?"
        confirmLabel="Delete"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    expect(screen.queryByText('Delete item')).not.toBeInTheDocument()
  })

  it('Cancel calls onClose, NOT onConfirm', () => {
    const onConfirm = vi.fn()
    const onClose = vi.fn()
    render(
      <DangerConfirmDialog
        open
        title="Delete item"
        description="Are you sure?"
        confirmLabel="Delete"
        onConfirm={onConfirm}
        onClose={onClose}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('Confirm calls onConfirm, NOT onClose (parent decides when to close)', () => {
    const onConfirm = vi.fn()
    const onClose = vi.fn()
    render(
      <DangerConfirmDialog
        open
        title="Delete item"
        description="Are you sure?"
        confirmLabel="Delete"
        onConfirm={onConfirm}
        onClose={onClose}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('disables both buttons while pending and shows pendingLabel on confirm', () => {
    render(
      <DangerConfirmDialog
        open
        title="Delete item"
        description="Are you sure?"
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    const confirmBtn = screen.getByRole('button', { name: 'Deleting…' })
    expect(confirmBtn).toBeDisabled()
  })

  it('falls back to "{confirmLabel}…" when no pendingLabel given', () => {
    render(
      <DangerConfirmDialog
        open
        title="Delete item"
        description="Are you sure?"
        confirmLabel="Remove"
        pending
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: 'Remove…' })).toBeInTheDocument()
  })

  it('renders ReactNode descriptions for interpolated content', () => {
    render(
      <DangerConfirmDialog
        open
        title="Remove role"
        description={<>Remove <strong>Hiring Manager</strong> from this user?</>}
        confirmLabel="Remove role"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText('Hiring Manager')).toBeInTheDocument()
  })
})
