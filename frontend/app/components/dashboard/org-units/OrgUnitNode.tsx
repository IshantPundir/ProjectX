import { memo, type CSSProperties, type KeyboardEvent } from 'react'
import { AlertCircle, Lock } from 'lucide-react'

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
  // is_accessible=false marks ancestors that the caller can see for tree
  // context but does not hold Admin on. Backend already 403s on writes
  // (_require_unit_admin in org_units/router.py); this card visually
  // reinforces that — greyed, locked, no context menu, no openRoles badge.
  const isLocked = !unit.is_accessible
  const pressure = isLocked ? null : pressureForOpenRoles(unit.openRoles)

  const dataState: 'selected' | 'on-path' | 'locked' | 'default' = isLocked
    ? 'locked'
    : isSelected
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
    // OS-standard "open context menu" shortcuts. Suppressed for locked
    // nodes so a non-admin can't open the create/delete radial menu.
    if (
      !isLocked &&
      (e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey))
    ) {
      e.preventDefault()
      onContextMenu?.(unit.id)
    }
  }

  const badgeStyle =
    pressure === 'hot'
      ? {
          background: 'var(--px-danger-bg)',
          color: 'var(--px-danger)',
          borderColor: 'var(--px-danger-line)',
        }
      : pressure === 'steady'
        ? {
            background: 'var(--px-caution-bg)',
            color: 'var(--px-caution)',
            borderColor: 'var(--px-caution-line)',
          }
        : {}

  const cardStyle: CSSProperties = {
    width: 168,
    height: 52,
    background: isLocked ? 'var(--px-bg-2)' : 'var(--px-surface)',
    borderRadius: 10,
    // Use longhand border-* properties only — mixing the `border`
    // shorthand with `borderStyle` triggers a React rerender warning
    // ("Updating a style property during rerender (border) when a
    // conflicting property is set (borderStyle)") because the shorthand
    // also sets border-style and the two write order is undefined.
    borderWidth: 1,
    borderStyle: isLocked ? 'dashed' : 'solid',
    borderColor: isLocked
      ? 'var(--px-hairline)'
      : isSelected
        ? 'var(--px-accent)'
        : isOnPath
          ? 'var(--px-accent-line)'
          : 'var(--px-hairline-strong)',
    boxShadow: isLocked
      ? 'none'
      : isSelected
        ? '0 0 0 3px var(--px-accent-glow)'
        : 'var(--px-shadow-sm)',
    display: 'flex',
    alignItems: 'center',
    paddingRight: 8,
    overflow: 'hidden',
    transition: 'box-shadow 120ms ease, border-color 120ms ease',
    cursor: isLocked ? 'not-allowed' : 'pointer',
    userSelect: 'none',
    opacity: isLocked ? 0.55 : 1,
  }

  return (
    <div
      role="button"
      tabIndex={0}
      data-node-card
      aria-label={
        isLocked
          ? `${unit.unit_type}: ${unit.name} (locked — ask an admin for access)`
          : `${unit.unit_type}: ${unit.name}`
      }
      aria-pressed={isSelected}
      aria-disabled={isLocked}
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
          {isLocked
            ? `${unit.unit_type} · locked`
            : `${unit.unit_type} · ${unit.member_count} members`}
        </span>
      </span>
      {isLocked && (
        <span
          data-testid="locked-icon"
          aria-hidden="true"
          className="ml-2 flex-none"
          title="Locked — you do not have admin access on this unit"
          style={{ color: 'var(--px-fg-4)' }}
        >
          <Lock size={12} strokeWidth={2} />
        </span>
      )}
      {/* ATS-imported client_account units land with a pending company
          profile; the recruiter must complete the 4-field profile before
          any imported JDs can be processed. Surface a caution glyph so
          the unit reads as "needs you" at a glance. */}
      {!isLocked &&
        unit.unit_type === 'client_account' &&
        unit.company_profile_completion_status === 'pending' && (
          <span
            data-testid="profile-incomplete-badge"
            aria-label="Imported from ATS — company profile incomplete"
            className="ml-2 flex-none"
            title="Imported from ATS. Complete the company profile to enable job creation."
            style={{ color: 'var(--px-caution)' }}
          >
            <AlertCircle size={12} strokeWidth={2} />
          </span>
        )}
      {pressure && (
        <span
          data-testid="open-roles-badge"
          className="ml-2 flex-none rounded-full border px-[7px] py-[2px] text-[10px] font-bold"
          style={badgeStyle}
        >
          {unit.openRoles}
        </span>
      )}
    </div>
  )
}

export const OrgUnitNode = memo(OrgUnitNodeImpl)
