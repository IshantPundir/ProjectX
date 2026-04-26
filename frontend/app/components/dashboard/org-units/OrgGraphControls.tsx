import type { CSSProperties } from 'react'
import { Maximize, Minus, Plus } from 'lucide-react'

interface Props {
  onZoomIn: () => void
  onZoomOut: () => void
  onFitView: () => void
}

const buttonStyle: CSSProperties = {
  width: 28,
  height: 28,
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: 'var(--px-surface)',
  color: 'var(--px-fg-2)',
  cursor: 'pointer',
}

export function OrgGraphControls({ onZoomIn, onZoomOut, onFitView }: Props) {
  return (
    <div
      data-no-pan
      role="group"
      aria-label="Canvas controls"
      style={{
        position: 'absolute',
        right: 12,
        bottom: 12,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        borderRadius: 6,
        border: '1px solid var(--px-hairline-strong)',
        boxShadow: 'var(--px-shadow-sm)',
        background: 'var(--px-surface)',
        zIndex: 10,
      }}
    >
      <button
        type="button"
        aria-label="Zoom in"
        onClick={onZoomIn}
        style={{ ...buttonStyle, borderBottom: '1px solid var(--px-hairline)' }}
      >
        <Plus size={14} aria-hidden strokeWidth={2} />
      </button>
      <button
        type="button"
        aria-label="Zoom out"
        onClick={onZoomOut}
        style={{ ...buttonStyle, borderBottom: '1px solid var(--px-hairline)' }}
      >
        <Minus size={14} aria-hidden strokeWidth={2} />
      </button>
      <button
        type="button"
        aria-label="Fit view"
        onClick={onFitView}
        style={buttonStyle}
      >
        <Maximize size={13} aria-hidden strokeWidth={2} />
      </button>
    </div>
  )
}
