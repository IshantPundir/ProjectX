import { describe, expect, it, vi } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'

import { renderWithProviders } from '../_utils/render'
import { OrgUnitContextMenu } from '@/components/dashboard/org-units/OrgUnitContextMenu'
import type { GraphNodeData } from '@/components/dashboard/org-units/OrgGraph'

function makeUnit(overrides: Partial<GraphNodeData> = {}): GraphNodeData {
  return {
    id: 'u1',
    client_id: 't1',
    parent_unit_id: null,
    name: 'North America',
    unit_type: 'region',
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

function defaultProps(overrides: object = {}) {
  return {
    target: { unit: makeUnit(), x: 200, y: 150 },
    allowedChildTypes: ['region', 'division', 'client_account', 'team'] as const,
    onClose: vi.fn(),
    onPickDelete: vi.fn(),
    onPickChild: vi.fn(),
    closing: false,
    onExitComplete: vi.fn(),
    ...overrides,
  }
}

describe('OrgUnitContextMenu', () => {
  it('renders Delete plus one pill per allowed child type', () => {
    renderWithProviders(<OrgUnitContextMenu {...defaultProps()} />)
    expect(screen.getByRole('menuitem', { name: /Delete North America/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /Add Region/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /Add Division/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /Add Client account/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /Add Team/i })).toBeInTheDocument()
  })

  it('omits Delete when admin_delete_disabled is true', () => {
    renderWithProviders(
      <OrgUnitContextMenu
        {...defaultProps({
          target: { unit: makeUnit({ admin_delete_disabled: true }), x: 0, y: 0 },
        })}
      />,
    )
    expect(screen.queryByRole('menuitem', { name: /Delete/i })).not.toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /Add Region/i })).toBeInTheDocument()
  })

  it('omits Delete for the root company unit (is_root mirrors backend invariant)', () => {
    renderWithProviders(
      <OrgUnitContextMenu
        {...defaultProps({
          target: {
            unit: makeUnit({
              is_root: true,
              unit_type: 'company',
              // admin_delete_disabled is intentionally false here — the
              // root invariant is independent of that flag.
              admin_delete_disabled: false,
            }),
            x: 0,
            y: 0,
          },
        })}
      />,
    )
    expect(screen.queryByRole('menuitem', { name: /Delete/i })).not.toBeInTheDocument()
  })

  it('renders only Delete when no child types are allowed (team leaf)', () => {
    renderWithProviders(
      <OrgUnitContextMenu
        {...defaultProps({ allowedChildTypes: [] })}
      />,
    )
    expect(screen.getByRole('menuitem', { name: /Delete/i })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: /Add/i })).not.toBeInTheDocument()
  })

  it('places items in a 180° right-side fan, centred on each slice', () => {
    renderWithProviders(<OrgUnitContextMenu {...defaultProps()} />)
    const items = screen.getAllByRole('menuitem')
    // 5 items spread across the right semicircle [0°, 180°], one per
    // slice of width 36°, each anchored at slice centre:
    //   angle_i = (i + 0.5) * 36
    expect(items).toHaveLength(5)
    expect(items[0].getAttribute('data-angle')).toBe('18')   // Delete (top-right)
    expect(items[1].getAttribute('data-angle')).toBe('54')
    expect(items[2].getAttribute('data-angle')).toBe('90')   // Centre (3 o'clock)
    expect(items[3].getAttribute('data-angle')).toBe('126')
    expect(items[4].getAttribute('data-angle')).toBe('162')  // Bottom-right
  })

  it('calls onPickDelete when the Delete pill is clicked', () => {
    const onPickDelete = vi.fn()
    renderWithProviders(<OrgUnitContextMenu {...defaultProps({ onPickDelete })} />)
    fireEvent.click(screen.getByRole('menuitem', { name: /Delete/i }))
    expect(onPickDelete).toHaveBeenCalledTimes(1)
  })

  it('calls onPickChild with the chosen unit type', () => {
    const onPickChild = vi.fn()
    renderWithProviders(<OrgUnitContextMenu {...defaultProps({ onPickChild })} />)
    fireEvent.click(screen.getByRole('menuitem', { name: /Add Division/i }))
    expect(onPickChild).toHaveBeenCalledWith('division')
  })

  it('calls onClose on Escape', () => {
    const onClose = vi.fn()
    renderWithProviders(<OrgUnitContextMenu {...defaultProps({ onClose })} />)
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('cycles focus with ArrowRight (clockwise) and ArrowLeft (counter-clockwise)', () => {
    renderWithProviders(<OrgUnitContextMenu {...defaultProps()} />)
    const items = screen.getAllByRole('menuitem')
    // First item is auto-focused on mount.
    expect(items[0]).toHaveFocus()
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowRight' })
    expect(items[1]).toHaveFocus()
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowLeft' })
    expect(items[0]).toHaveFocus()
    // Wrap counter-clockwise: at index 0, Left → last
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowLeft' })
    expect(items[items.length - 1]).toHaveFocus()
  })

  it('exposes a menu role with the unit name in the aria-label', () => {
    renderWithProviders(<OrgUnitContextMenu {...defaultProps()} />)
    expect(screen.getByRole('menu')).toHaveAttribute(
      'aria-label',
      'Actions for North America',
    )
  })
})
