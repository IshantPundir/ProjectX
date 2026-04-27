import { describe, expect, it, vi } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'

import { renderWithProviders } from '../_utils/render'
import { OrgUnitNode } from '@/components/dashboard/org-units/OrgUnitNode'
import type { GraphNodeData } from '@/components/dashboard/org-units/OrgGraph'

function makeUnit(overrides: Partial<GraphNodeData> = {}): GraphNodeData {
  return {
    id: 'u1',
    client_id: 't1',
    parent_unit_id: null,
    name: 'Engineering',
    unit_type: 'division',
    member_count: 5,
    created_at: '2026-04-01T00:00:00Z',
    created_by: null,
    created_by_email: null,
    deletable_by: null,
    deletable_by_email: null,
    admin_delete_disabled: false,
    is_accessible: true,
    admin_emails: [],
    is_root: false,
    company_profile: null,
    company_profile_completed_at: null,
    metadata: null,
    inherited_locale: null,
    inherited_compliance: null,
    openRoles: 0,
    pressure: 'cool',
    ...overrides,
  }
}

function renderNode(opts: {
  unit?: Partial<GraphNodeData>
  selectedId?: string | null
  onSelectPath?: Set<string>
  onSelect?: (id: string) => void
  onContextMenu?: (id: string) => void
} = {}) {
  const unit = makeUnit(opts.unit)
  const onSelect = opts.onSelect ?? vi.fn()
  const onContextMenu = opts.onContextMenu ?? vi.fn()
  const utils = renderWithProviders(
    <OrgUnitNode
      unit={unit}
      selectedId={opts.selectedId ?? null}
      onSelectPath={opts.onSelectPath ?? new Set<string>()}
      onSelect={onSelect}
      onContextMenu={onContextMenu}
    />,
  )
  return { ...utils, unit, onSelect, onContextMenu }
}

describe('OrgUnitNode', () => {
  it('renders the unit name', () => {
    renderNode()
    expect(screen.getByText('Engineering')).toBeInTheDocument()
  })

  it('renders the type-and-member-count subtitle', () => {
    renderNode()
    expect(screen.getByText(/division\s+·\s+5 members/)).toBeInTheDocument()
  })

  it('hides the open-roles badge when openRoles is 0', () => {
    renderNode()
    expect(screen.queryByTestId('open-roles-badge')).not.toBeInTheDocument()
  })

  it('renders an amber-styled badge for openRoles 1–2', () => {
    renderNode({ unit: { openRoles: 2 } })
    const badge = screen.getByTestId('open-roles-badge')
    expect(badge).toHaveTextContent('2')
    expect(badge.className).toMatch(/amber/)
  })

  it('renders a red-styled badge for openRoles 3+', () => {
    renderNode({ unit: { openRoles: 5 } })
    const badge = screen.getByTestId('open-roles-badge')
    expect(badge).toHaveTextContent('5')
    expect(badge.className).toMatch(/red/)
  })

  it('calls onSelect with the unit id on click', () => {
    const { onSelect } = renderNode()
    fireEvent.click(screen.getByRole('button', { name: /division: Engineering/ }))
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith('u1')
  })

  it('calls onSelect on Enter and Space keypress', () => {
    const { onSelect } = renderNode()
    const card = screen.getByRole('button', { name: /division: Engineering/ })
    card.focus()
    fireEvent.keyDown(card, { key: 'Enter' })
    fireEvent.keyDown(card, { key: ' ' })
    expect(onSelect).toHaveBeenCalledTimes(2)
  })

  it('exposes data-state="selected" when selectedId matches', () => {
    renderNode({ selectedId: 'u1' })
    expect(screen.getByRole('button')).toHaveAttribute('data-state', 'selected')
    expect(screen.getByRole('button')).toHaveAttribute('aria-pressed', 'true')
  })

  it('exposes data-state="on-path" when in selectedPath but not selected', () => {
    renderNode({ selectedId: 'other', onSelectPath: new Set(['u1']) })
    expect(screen.getByRole('button')).toHaveAttribute('data-state', 'on-path')
  })

  it('falls back to team style and warns for an unknown unit_type', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    renderNode({
      unit: { unit_type: 'totally_unknown' as GraphNodeData['unit_type'] },
    })
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('unknown unit_type'))
    warn.mockRestore()
  })

  it('calls data.onContextMenu on Shift+F10', () => {
    const onContextMenu = vi.fn()
    renderNode({ onContextMenu })
    const card = screen.getByRole('button', { name: /division: Engineering/ })
    card.focus()
    fireEvent.keyDown(card, { key: 'F10', shiftKey: true })
    expect(onContextMenu).toHaveBeenCalledTimes(1)
    expect(onContextMenu).toHaveBeenCalledWith('u1')
  })

  it('calls data.onContextMenu on the ContextMenu key', () => {
    const onContextMenu = vi.fn()
    renderNode({ onContextMenu })
    const card = screen.getByRole('button', { name: /division: Engineering/ })
    card.focus()
    fireEvent.keyDown(card, { key: 'ContextMenu' })
    expect(onContextMenu).toHaveBeenCalledTimes(1)
  })

  it('marks the card with data-node-card so the pan hook can skip it', () => {
    renderNode()
    const card = screen.getByRole('button', { name: /division: Engineering/ })
    expect(card.hasAttribute('data-node-card')).toBe(true)
  })
})
