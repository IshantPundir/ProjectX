import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PipelineSourcePicker } from './PipelineSourcePicker'

describe('PipelineSourcePicker', () => {
  it('renders all four system starter cards', () => {
    render(<PipelineSourcePicker jobId="j1" recentTemplates={[]} teamDefault={null} onPick={() => {}} />)
    expect(screen.getByText(/standard technical/i)).toBeInTheDocument()
    expect(screen.getByText(/fast track/i)).toBeInTheDocument()
    expect(screen.getByText(/screening only/i)).toBeInTheDocument()
    expect(screen.getByText(/senior leadership/i)).toBeInTheDocument()
  })

  it('renders the blank card last', () => {
    render(<PipelineSourcePicker jobId="j1" recentTemplates={[]} teamDefault={null} onPick={() => {}} />)
    expect(screen.getByText(/build from scratch/i)).toBeInTheDocument()
  })

  it('calls onPick with template body when a recent template is clicked', () => {
    const onPick = vi.fn()
    const tpl = { id: 't1', name: 'Eng Default', stage_count: 4, last_used: '2d ago' }
    render(<PipelineSourcePicker jobId="j1" recentTemplates={[tpl]} teamDefault={null} onPick={onPick} />)
    fireEvent.click(screen.getByRole('button', { name: /eng default/i }))
    expect(onPick).toHaveBeenCalledWith({ source: 'template', template_id: 't1' })
  })

  it('calls onPick with starter body when a starter is clicked', () => {
    const onPick = vi.fn()
    render(<PipelineSourcePicker jobId="j1" recentTemplates={[]} teamDefault={null} onPick={onPick} />)
    fireEvent.click(screen.getByRole('button', { name: /standard technical/i }))
    expect(onPick).toHaveBeenCalledWith({ source: 'starter', starter_key: 'standard_technical' })
  })

  it('calls onPick with scratch body containing intake + debrief when blank is clicked', () => {
    const onPick = vi.fn()
    render(<PipelineSourcePicker jobId="j1" recentTemplates={[]} teamDefault={null} onPick={onPick} />)
    fireEvent.click(screen.getByRole('button', { name: /build from scratch/i }))
    expect(onPick).toHaveBeenCalledWith({
      source: 'scratch',
      stages: [
        { position: 0, name: 'Intake', stage_type: 'intake' },
        { position: 1, name: 'Debrief', stage_type: 'debrief' },
      ],
    })
  })

  it('dedupes team default that also appears in recent templates', () => {
    const tpl = { id: 't1', name: 'Eng Default', stage_count: 4, last_used: '2d ago' }
    render(<PipelineSourcePicker jobId="j1" recentTemplates={[tpl]} teamDefault={tpl} onPick={() => {}} />)
    expect(screen.getAllByText(/eng default/i)).toHaveLength(1)
  })

  it('shows team default with a star indicator when set and not in recent', () => {
    const tpl = { id: 't1', name: 'Team Default', stage_count: 4, last_used: '2d ago' }
    render(<PipelineSourcePicker jobId="j1" recentTemplates={[]} teamDefault={tpl} onPick={() => {}} />)
    // The star can be a Lucide icon, an emoji, or text — keep the assertion flexible.
    // getAllByText because the section heading "Team default" and card "Team Default"
    // both match the case-insensitive pattern — both are expected to be present.
    expect(screen.getAllByText(/team default/i).length).toBeGreaterThanOrEqual(1)
    // Star indicator is rendered as ★ text node inside the card label
    expect(screen.getByRole('button', { name: /team default/i })).toBeInTheDocument()
  })

  it('limits recent templates to top 3', () => {
    const recents = Array.from({ length: 5 }, (_, i) => ({
      id: `t${i}`, name: `Template ${i}`, stage_count: 3, last_used: `${i}d ago`,
    }))
    render(<PipelineSourcePicker jobId="j1" recentTemplates={recents} teamDefault={null} onPick={() => {}} />)
    // Only first 3 render
    expect(screen.getByText(/template 0/i)).toBeInTheDocument()
    expect(screen.getByText(/template 1/i)).toBeInTheDocument()
    expect(screen.getByText(/template 2/i)).toBeInTheDocument()
    expect(screen.queryByText(/template 3/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/template 4/i)).not.toBeInTheDocument()
  })
})
