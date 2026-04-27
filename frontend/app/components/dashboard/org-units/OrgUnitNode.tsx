import { memo, type CSSProperties, type KeyboardEvent } from 'react'

import type { GraphNodeData } from './OrgGraph'
import { getUnitTypeStyle } from './unit-type-style'

export interface OrgUnitNodeProps {
  unit: GraphNodeData
  selectedId: string | null
  onSelectPath: Set<string>
  onSelect: (id: string) => void
  onContextMenu?: (id: string) => void
}

type Pressure = 'hot' | 'steady' | null

function pressureForOpenRoles(openRoles: number): Pressure {
  if (openRoles >= 3) return 'hot'
  if (openRoles > 0) return 'steady'
  return null
}

function OrgUnitNodeImpl({
  unit,
  selectedId,
  onSelectPath,
  onSelect,
  onContextMenu,
}: OrgUnitNodeProps) {
  const style = getUnitTypeStyle(unit.unit_type)
  const Icon = style.icon

  const isSelected = selectedId === unit.id
  const isOnPath = !isSelected && onSelectPath.has(unit.id)
  const pressure = pressureForOpenRoles(unit.openRoles)

  const dataState: 'selected' | 'on-path' | 'default' = isSelected
    ? 'selected'
    : isOnPath
      ? 'on-path'
      : 'default'

  function handleKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onSelect(unit.id)
      return
    }
    // OS-standard "open context menu" shortcuts.
    if (e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) {
      e.preventDefault()
      onContextMenu?.(unit.id)
    }
  }

  const badgeClass =
    pressure === 'hot'
      ? 'bg-red-50 text-red-700 border-red-200'
      : pressure === 'steady'
        ? 'bg-amber-50 text-amber-700 border-amber-200'
        : ''

  const cardStyle: CSSProperties = {
    width: 168,
    height: 52,
    background: 'var(--px-surface)',
    borderRadius: 10,
    border: `1px solid ${
      isSelected
        ? 'var(--px-accent)'
        : isOnPath
          ? 'var(--px-accent-line)'
          : 'var(--px-hairline-strong)'
    }`,
    boxShadow: isSelected
      ? '0 0 0 3px var(--px-accent-glow)'
      : 'var(--px-shadow-sm)',
    display: 'flex',
    alignItems: 'center',
    paddingRight: 8,
    overflow: 'hidden',
    transition: 'box-shadow 120ms ease, border-color 120ms ease',
    cursor: 'pointer',
    userSelect: 'none',
  }

  return (
    <div
      role="button"
      tabIndex={0}
      data-node-card
      aria-label={`${unit.unit_type}: ${unit.name}`}
      aria-pressed={isSelected}
      data-state={dataState}
      className="focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--px-accent)]"
      style={cardStyle}
      onClick={() => onSelect(unit.id)}
      onKeyDown={handleKey}
    >
      <span
        aria-hidden="true"
        style={{
          width: 4,
          alignSelf: 'stretch',
          background: style.stripVar,
          borderRadius: '10px 0 0 10px',
          marginRight: 10,
          flex: 'none',
        }}
      />
      <span
        aria-hidden="true"
        style={{
          width: 28,
          height: 28,
          borderRadius: 7,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flex: 'none',
          marginRight: 9,
          background: style.bgVar,
          border: `1px solid ${style.lineVar}`,
        }}
      >
        <Icon size={16} color={style.stripVar} strokeWidth={1.8} aria-hidden />
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>
        <span
          style={{
            display: 'block',
            fontSize: 13,
            fontWeight: 600,
            color: 'var(--px-fg)',
            lineHeight: 1.15,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {unit.name}
        </span>
        <span
          style={{
            display: 'block',
            fontSize: 10.5,
            color: 'var(--px-fg-3)',
            marginTop: 2,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {unit.unit_type} &middot; {unit.member_count} members
        </span>
      </span>
      {pressure && (
        <span
          data-testid="open-roles-badge"
          className={`ml-2 flex-none rounded-full border px-[7px] py-[2px] text-[10px] font-bold ${badgeClass}`}
        >
          {unit.openRoles}
        </span>
      )}
    </div>
  )
}

export const OrgUnitNode = memo(OrgUnitNodeImpl)
