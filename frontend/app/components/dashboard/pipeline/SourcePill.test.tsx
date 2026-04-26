import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SourcePill } from './SourcePill'

describe('SourcePill', () => {
  it('renders custom (no source) pill when both ids are null', () => {
    render(<SourcePill
      sourceTemplateId={null} sourceTemplateName={null} sourceStarterKey={null}
      diverged={false} canSwap={true} canUpdateSource={false}
      onReset={() => {}} onSwap={() => {}} onSaveAsTemplate={() => {}} onUpdateSourceTemplate={() => {}}
    />)
    expect(screen.getByText(/custom/i)).toBeInTheDocument()
  })

  it('renders template-name pill when sourceTemplateId is set', () => {
    render(<SourcePill
      sourceTemplateId="t1" sourceTemplateName="Eng Default" sourceStarterKey={null}
      diverged={false} canSwap={true} canUpdateSource={true}
      onReset={() => {}} onSwap={() => {}} onSaveAsTemplate={() => {}} onUpdateSourceTemplate={() => {}}
    />)
    expect(screen.getByText(/eng default/i)).toBeInTheDocument()
    expect(screen.getByText(/team template/i)).toBeInTheDocument()
  })

  it('renders starter-key pill when sourceStarterKey is set', () => {
    render(<SourcePill
      sourceTemplateId={null} sourceTemplateName={null} sourceStarterKey="standard_technical"
      diverged={false} canSwap={true} canUpdateSource={false}
      onReset={() => {}} onSwap={() => {}} onSaveAsTemplate={() => {}} onUpdateSourceTemplate={() => {}}
    />)
    expect(screen.getByText(/standard_technical|standard technical/i)).toBeInTheDocument()
    expect(screen.getByText(/system starter/i)).toBeInTheDocument()
  })

  it('shows Edited pill when diverged is true', () => {
    render(<SourcePill
      sourceTemplateId="t1" sourceTemplateName="X" sourceStarterKey={null}
      diverged={true} canSwap={true} canUpdateSource={true}
      onReset={() => {}} onSwap={() => {}} onSaveAsTemplate={() => {}} onUpdateSourceTemplate={() => {}}
    />)
    expect(screen.getByText(/edited/i)).toBeInTheDocument()
  })

  it('does not show Edited pill when diverged is false', () => {
    render(<SourcePill
      sourceTemplateId="t1" sourceTemplateName="X" sourceStarterKey={null}
      diverged={false} canSwap={true} canUpdateSource={true}
      onReset={() => {}} onSwap={() => {}} onSaveAsTemplate={() => {}} onUpdateSourceTemplate={() => {}}
    />)
    expect(screen.queryByText(/edited/i)).not.toBeInTheDocument()
  })

  it('clicking Save as new template fires onSaveAsTemplate', () => {
    const onSave = vi.fn()
    render(<SourcePill
      sourceTemplateId="t1" sourceTemplateName="X" sourceStarterKey={null}
      diverged={false} canSwap={true} canUpdateSource={true}
      onReset={() => {}} onSwap={() => {}} onSaveAsTemplate={onSave} onUpdateSourceTemplate={() => {}}
    />)
    // Open the kebab menu first
    fireEvent.click(screen.getByRole('button', { name: /more|menu|options/i }))
    fireEvent.click(screen.getByRole('menuitem', { name: /save as new template/i }))
    expect(onSave).toHaveBeenCalledTimes(1)
  })

  it('Update source template menu item is disabled when canUpdateSource is false', () => {
    render(<SourcePill
      sourceTemplateId={null} sourceTemplateName={null} sourceStarterKey="standard_technical"
      diverged={false} canSwap={true} canUpdateSource={false}
      onReset={() => {}} onSwap={() => {}} onSaveAsTemplate={() => {}} onUpdateSourceTemplate={() => {}}
    />)
    fireEvent.click(screen.getByRole('button', { name: /more|menu|options/i }))
    const updateItem = screen.queryByRole('menuitem', { name: /update source template/i })
    if (updateItem) {
      expect(updateItem).toHaveAttribute('aria-disabled', 'true')
    }
    // If it's not rendered at all, that's also acceptable behavior
  })
})
