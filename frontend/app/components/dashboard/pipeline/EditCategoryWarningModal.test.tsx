import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { EditCategoryWarningModal } from './EditCategoryWarningModal'

describe('EditCategoryWarningModal', () => {
  it('Category B shows shape-change copy and Confirm button', () => {
    render(<EditCategoryWarningModal
      open={true} onOpenChange={() => {}}
      category="B" inFlightCounts={{}}
      onConfirm={() => {}}
    />)
    expect(screen.getByText(/changes the pipeline shape/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /confirm/i })).toBeInTheDocument()
  })

  it('Category C with no in-flight shows remove copy', () => {
    render(<EditCategoryWarningModal
      open={true} onOpenChange={() => {}}
      category="C" inFlightCounts={{ s1: 0 }}
      onConfirm={() => {}}
    />)
    // Title and body are separate elements — check at least one is present
    const matches = screen.getAllByText(/remove this stage|permanent/i)
    expect(matches.length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /confirm.*remove|remove/i })).toBeInTheDocument()
  })

  it('Category C with in-flight shows pause-first copy and Pause Stage button', () => {
    const onPause = vi.fn()
    render(<EditCategoryWarningModal
      open={true} onOpenChange={() => {}}
      category="C" inFlightCounts={{ s1: 3 }}
      onConfirm={() => {}}
      onPause={onPause}
    />)
    expect(screen.getByText(/3 candidates/i)).toBeInTheDocument()
    expect(screen.getByText(/pausing this stage/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /pause stage/i }))
    expect(onPause).toHaveBeenCalledTimes(1)
  })

  it('Confirm button calls onConfirm for Category B', () => {
    const onConfirm = vi.fn()
    render(<EditCategoryWarningModal
      open={true} onOpenChange={() => {}}
      category="B" inFlightCounts={{}}
      onConfirm={onConfirm}
    />)
    fireEvent.click(screen.getByRole('button', { name: /confirm/i }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('does not render when open=false', () => {
    const { container } = render(<EditCategoryWarningModal
      open={false} onOpenChange={() => {}}
      category="B" inFlightCounts={{}}
      onConfirm={() => {}}
    />)
    expect(screen.queryByText(/changes the pipeline shape/i)).not.toBeInTheDocument()
  })
})
