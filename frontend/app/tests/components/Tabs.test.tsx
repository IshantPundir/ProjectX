import { describe, it, expect, vi } from 'vitest'
import { fireEvent } from '@testing-library/react'
import { renderWithProviders } from '../_utils/render'
import { Tabs } from '@/components/px/Tabs'

describe('Tabs primitive', () => {
  const items = [
    { value: 'a', label: 'A' },
    { value: 'b', label: 'B' },
    { value: 'c', label: 'C' },
  ]

  it('renders all items and marks the selected one as active (aria-selected=true)', () => {
    const { getByRole } = renderWithProviders(
      <Tabs value="b" onChange={() => {}} items={items} ariaLabel="Test tabs" />,
    )
    expect(getByRole('tab', { name: 'A' }).getAttribute('aria-selected')).toBe('false')
    expect(getByRole('tab', { name: 'B' }).getAttribute('aria-selected')).toBe('true')
    expect(getByRole('tab', { name: 'C' }).getAttribute('aria-selected')).toBe('false')
  })

  it('calls onChange when a non-disabled tab is clicked', () => {
    const onChange = vi.fn()
    const { getByRole } = renderWithProviders(
      <Tabs value="a" onChange={onChange} items={items} ariaLabel="Test tabs" />,
    )
    fireEvent.click(getByRole('tab', { name: 'C' }))
    expect(onChange).toHaveBeenCalledWith('c')
  })

  it('does not call onChange when a disabled tab is clicked', () => {
    const onChange = vi.fn()
    const itemsWithDisabled = [
      { value: 'a', label: 'A' },
      { value: 'b', label: 'B', disabled: true },
    ]
    const { getByRole } = renderWithProviders(
      <Tabs value="a" onChange={onChange} items={itemsWithDisabled} ariaLabel="Test tabs" />,
    )
    fireEvent.click(getByRole('tab', { name: 'B' }))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('does not render hidden items', () => {
    const itemsWithHidden = [
      { value: 'a', label: 'A' },
      { value: 'b', label: 'B', hidden: true },
      { value: 'c', label: 'C' },
    ]
    const { queryByRole } = renderWithProviders(
      <Tabs value="a" onChange={() => {}} items={itemsWithHidden} ariaLabel="Test tabs" />,
    )
    expect(queryByRole('tab', { name: 'B' })).toBeNull()
    expect(queryByRole('tab', { name: 'C' })).not.toBeNull()
  })
})
