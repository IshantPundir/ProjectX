'use client'

import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from 'react'

import { UNIT_TYPE_STYLE, type UnitType } from './unit-type-style'

const TYPE_LABEL: Record<UnitType, string> = {
  company: 'Company',
  client_account: 'Client account',
  region: 'Region',
  division: 'Division',
  team: 'Team',
}

interface Props {
  unitType: UnitType
  /** Pivot in canvas-local coordinates. */
  x: number
  y: number
  onSubmit: (name: string) => void
  onCancel: () => void
  pending?: boolean
  error?: string | null
}

export function OrgUnitInlineCreate({
  unitType,
  x,
  y,
  onSubmit,
  onCancel,
  pending = false,
  error = null,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [value, setValue] = useState('')

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Escape') {
      e.preventDefault()
      onCancel()
      return
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      const trimmed = value.trim()
      if (trimmed.length === 0) return
      onSubmit(trimmed)
    }
  }

  const style = UNIT_TYPE_STYLE[unitType]
  const label = TYPE_LABEL[unitType]

  const rootStyle: CSSProperties = {
    position: 'absolute',
    left: x,
    top: y,
    transform: 'translate(-50%, -50%)',
    background: 'var(--px-surface)',
    border: '1px solid var(--px-accent-line)',
    boxShadow:
      '0 0 0 3px var(--px-accent-glow), 0 8px 24px rgba(58, 45, 28, 0.08), 0 2px 4px rgba(58, 45, 28, 0.04)',
    borderRadius: 10,
    padding: '8px 10px',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    pointerEvents: 'auto',
    zIndex: 50,
  }

  return (
    <div
      onContextMenu={(e) => e.preventDefault()}
      onMouseDown={(e) => e.stopPropagation()}
      style={rootStyle}
    >
      <span
        aria-hidden="true"
        style={{
          fontSize: 10.5,
          fontWeight: 700,
          textTransform: 'uppercase',
          letterSpacing: '0.04em',
          color: style.stripVar,
          background: style.bgVar,
          padding: '3px 7px',
          borderRadius: 999,
          border: `1px solid ${style.lineVar}`,
          whiteSpace: 'nowrap',
        }}
      >
        + {label}
      </span>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={pending}
          aria-label={`Name the new ${label.toLowerCase()}`}
          placeholder={`Name the new ${label.toLowerCase()}…`}
          style={{
            border: '1px solid var(--px-hairline-strong)',
            background: 'var(--px-bg)',
            borderRadius: 6,
            padding: '5px 8px',
            fontSize: 12,
            width: 200,
            color: 'var(--px-fg)',
            outline: 'none',
            fontFamily: 'inherit',
          }}
        />
        {error && (
          <span
            role="alert"
            style={{
              fontSize: 10.5,
              color: 'var(--color-red-700)',
            }}
          >
            {error}
          </span>
        )}
      </div>
      <span
        aria-hidden="true"
        style={{
          fontSize: 10,
          color: 'var(--px-fg-4)',
          fontFamily: 'ui-monospace, "JetBrains Mono", monospace',
          whiteSpace: 'nowrap',
        }}
      >
        ⏎ create · esc cancel
      </span>
    </div>
  )
}
