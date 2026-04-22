"use client";

import { useMemo } from "react";
import type { OrgUnit } from "@/lib/api/org-units";

/**
 * OrgGraph — horizontal node-link tree rendered as SVG.
 *
 * Port of v4/OrgSettings.jsx OrgGraph. Pixel-perfect to the design:
 * - depth 0 (company/client_account) → rounded square
 * - depth 1 (region/division) → hexagon
 * - depth ≥ 2 (team, nested divisions) → circle
 * - pressure ring color from `pressure` per unit
 * - open-roles badge in top-right of each node
 * - selected-path highlighted up to the root
 *
 * Interactions:
 * - onSelect(id) when a node is clicked
 * - onHover(id|null) when pointer enters/leaves a node
 */

const OG_W = 1340;
const OG_H = 460;

export type Pressure = "hot" | "steady" | "cool";

export interface GraphNodeData extends OrgUnit {
  openRoles: number;
  pressure: Pressure;
}

interface Props {
  units: GraphNodeData[];
  selectedId: string | null;
  hoverId: string | null;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
}

export function OrgGraph({
  units,
  selectedId,
  hoverId,
  onSelect,
  onHover,
}: Props) {
  const layout = useMemo(() => {
    const byId = Object.fromEntries(units.map((u) => [u.id, u])) as Record<
      string,
      GraphNodeData
    >;
    const childrenOf: Record<string, string[]> = {};
    for (const u of units) {
      if (u.parent_unit_id) {
        (childrenOf[u.parent_unit_id] ||= []).push(u.id);
      }
    }
    const depth: Record<string, number> = {};
    const computeDepth = (id: string, d = 0) => {
      depth[id] = d;
      for (const cid of childrenOf[id] || []) computeDepth(cid, d + 1);
    };
    for (const u of units) if (!u.parent_unit_id) computeDepth(u.id, 0);

    const depths = Object.values(depth);
    const maxDepth = depths.length > 0 ? Math.max(...depths) : 0;
    const colX = (d: number) =>
      90 + d * ((OG_W - 180) / Math.max(1, maxDepth));

    const yPos: Record<string, number> = {};
    let yCounter = 0;
    const walk = (id: string) => {
      const kids = childrenOf[id] || [];
      if (kids.length === 0) {
        yPos[id] = yCounter++;
      } else {
        for (const k of kids) walk(k);
        const ys = kids.map((k) => yPos[k]);
        yPos[id] = (Math.min(...ys) + Math.max(...ys)) / 2;
      }
    };
    for (const u of units) if (!u.parent_unit_id) walk(u.id);
    const totalLeaves = yCounter;

    const yFor = (id: string) => {
      const slot = yPos[id];
      if (slot === undefined) return OG_H / 2;
      const pad = 60;
      return pad + (slot / Math.max(1, totalLeaves - 1)) * (OG_H - pad * 2);
    };

    const edges = units
      .filter((u) => u.parent_unit_id)
      .map((u) => ({
        from: u.parent_unit_id!,
        to: u.id,
        x1: colX(depth[u.parent_unit_id!]),
        y1: yFor(u.parent_unit_id!),
        x2: colX(depth[u.id]),
        y2: yFor(u.id),
      }));

    const selectedPath = new Set<string>();
    let cur = selectedId;
    while (cur) {
      selectedPath.add(cur);
      cur = byId[cur]?.parent_unit_id ?? null;
    }

    return { depth, colX, yFor, edges, selectedPath, byId };
  }, [units, selectedId]);

  const { depth, colX, yFor, edges, selectedPath } = layout;

  return (
    <div className="absolute inset-0 overflow-hidden">
      <svg
        width="100%"
        height="100%"
        viewBox={`0 0 ${OG_W} ${OG_H}`}
        preserveAspectRatio="xMidYMid meet"
        className="block"
      >
        <defs>
          <pattern
            id="og-grid"
            width="40"
            height="40"
            patternUnits="userSpaceOnUse"
          >
            <circle cx="1" cy="1" r="0.8" fill="var(--px-fg-4)" opacity="0.14" />
          </pattern>
        </defs>
        <rect width={OG_W} height={OG_H} fill="url(#og-grid)" />

        {edges.map((e, i) => {
          const onPath =
            selectedPath.has(e.from) && selectedPath.has(e.to);
          const cx = (e.x1 + e.x2) / 2;
          const d = `M ${e.x1} ${e.y1} C ${cx} ${e.y1}, ${cx} ${e.y2}, ${e.x2} ${e.y2}`;
          return (
            <path
              key={i}
              d={d}
              fill="none"
              stroke={onPath ? "var(--px-accent)" : "var(--px-hairline-strong)"}
              strokeWidth={onPath ? 1.8 : 1}
              opacity={onPath ? 0.9 : 0.55}
            />
          );
        })}

        {units.map((u) => (
          <OrgNode
            key={u.id}
            unit={u}
            depth={depth[u.id] ?? 0}
            x={colX(depth[u.id] ?? 0)}
            y={yFor(u.id)}
            selected={selectedId === u.id}
            onPath={selectedPath.has(u.id)}
            hovered={hoverId === u.id}
            onSelect={onSelect}
            onHover={onHover}
          />
        ))}
      </svg>
    </div>
  );
}

function OrgNode({
  unit,
  depth,
  x,
  y,
  selected,
  onPath,
  hovered,
  onSelect,
  onHover,
}: {
  unit: GraphNodeData;
  depth: number;
  x: number;
  y: number;
  selected: boolean;
  onPath: boolean;
  hovered: boolean;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
}) {
  const pressureColor: Record<Pressure, string> = {
    hot: "var(--px-accent)",
    steady: "var(--px-ok)",
    cool: "var(--px-fg-4)",
  };
  const ring = pressureColor[unit.pressure];

  const base = selected ? 24 : hovered ? 22 : 20;
  const r = base;
  const labelOffset = r + 14;

  const fill = selected
    ? "var(--px-accent)"
    : onPath
      ? "var(--px-accent-tint)"
      : "var(--px-surface)";
  const stroke = selected
    ? "var(--px-accent)"
    : onPath
      ? "var(--px-accent-line)"
      : "var(--px-hairline-strong)";

  const hex = (rad: number) => {
    const pts: string[] = [];
    for (let i = 0; i < 6; i++) {
      const a = (Math.PI / 3) * i - Math.PI / 2;
      pts.push(
        `${(rad * Math.cos(a)).toFixed(2)},${(rad * Math.sin(a)).toFixed(2)}`,
      );
    }
    return pts.join(" ");
  };

  return (
    <g
      transform={`translate(${x}, ${y})`}
      className="cursor-pointer"
      onClick={() => onSelect(unit.id)}
      onMouseEnter={() => onHover(unit.id)}
      onMouseLeave={() => onHover(null)}
    >
      {/* Pressure ring */}
      {depth === 0 && (
        <rect
          x={-(r + 5)}
          y={-(r + 5)}
          width={(r + 5) * 2}
          height={(r + 5) * 2}
          rx={8}
          fill="none"
          stroke={ring}
          strokeWidth={selected ? 2 : 1.3}
          opacity={selected ? 1 : 0.55}
        />
      )}
      {depth === 1 && (
        <polygon
          points={hex(r + 5)}
          fill="none"
          stroke={ring}
          strokeWidth={selected ? 2 : 1.3}
          opacity={selected ? 1 : 0.55}
        />
      )}
      {depth >= 2 && (
        <circle
          r={r + 5}
          fill="none"
          stroke={ring}
          strokeWidth={selected ? 2 : 1.3}
          opacity={selected ? 1 : 0.55}
        />
      )}

      {/* Body */}
      {depth === 0 && (
        <rect
          x={-r}
          y={-r}
          width={r * 2}
          height={r * 2}
          rx={6}
          fill={fill}
          stroke={stroke}
          strokeWidth={1.5}
        />
      )}
      {depth === 1 && (
        <polygon points={hex(r)} fill={fill} stroke={stroke} strokeWidth={1.5} />
      )}
      {depth >= 2 && (
        <circle r={r} fill={fill} stroke={stroke} strokeWidth={1.5} />
      )}

      {/* Headcount */}
      <text
        textAnchor="middle"
        dominantBaseline="central"
        y={1}
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          fontWeight: 600,
          fill: selected ? "#fff" : "var(--px-fg)",
          fontVariantNumeric: "tabular-nums",
          pointerEvents: "none",
          userSelect: "none",
        }}
      >
        {unit.member_count}
      </text>

      {/* Open-roles badge */}
      {unit.openRoles > 0 && (
        <g transform={`translate(${r - 4}, ${-r + 4})`}>
          <circle
            r={8}
            fill="var(--px-accent)"
            stroke="var(--px-surface)"
            strokeWidth={1.5}
          />
          <text
            textAnchor="middle"
            dominantBaseline="central"
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 9,
              fontWeight: 700,
              fill: "#fff",
              pointerEvents: "none",
              userSelect: "none",
            }}
          >
            {unit.openRoles}
          </text>
        </g>
      )}

      {/* Labels */}
      <text
        textAnchor="middle"
        y={labelOffset + 8}
        style={{
          fontFamily: "var(--font-sans)",
          fontSize: 12,
          fontWeight: selected ? 600 : 500,
          fill: selected || onPath ? "var(--px-fg)" : "var(--px-fg-2)",
          pointerEvents: "none",
          userSelect: "none",
        }}
      >
        {unit.name}
      </text>
    </g>
  );
}

export function OrgLegend() {
  return (
    <div
      className="inline-flex items-center gap-3.5 rounded-full border px-3 py-1.5 text-[11px]"
      style={{
        background: "var(--px-surface)",
        borderColor: "var(--px-hairline)",
      }}
    >
      <span
        className="inline-flex items-center gap-1.5"
        style={{ color: "var(--px-fg-3)" }}
      >
        <svg width="12" height="12" viewBox="-7 -7 14 14">
          <rect
            x={-5}
            y={-5}
            width={10}
            height={10}
            rx={2}
            fill="none"
            stroke="var(--px-fg-3)"
            strokeWidth={1.4}
          />
        </svg>
        company
      </span>
      <span
        className="inline-flex items-center gap-1.5"
        style={{ color: "var(--px-fg-3)" }}
      >
        <svg width="14" height="14" viewBox="-7 -7 14 14">
          <polygon
            points="0,-5.5 4.76,-2.75 4.76,2.75 0,5.5 -4.76,2.75 -4.76,-2.75"
            fill="none"
            stroke="var(--px-fg-3)"
            strokeWidth={1.4}
          />
        </svg>
        division
      </span>
      <span
        className="inline-flex items-center gap-1.5"
        style={{ color: "var(--px-fg-3)" }}
      >
        <svg width="12" height="12" viewBox="-7 -7 14 14">
          <circle r={5} fill="none" stroke="var(--px-fg-3)" strokeWidth={1.4} />
        </svg>
        team
      </span>
      <span
        className="h-3 w-px"
        style={{ background: "var(--px-hairline)" }}
      />
      {(
        [
          { label: "hiring hot", color: "var(--px-accent)" },
          { label: "steady", color: "var(--px-ok)" },
          { label: "cool", color: "var(--px-fg-4)" },
        ] as const
      ).map((i) => (
        <span
          key={i.label}
          className="inline-flex items-center gap-1.5"
          style={{ color: "var(--px-fg-3)" }}
        >
          <span
            className="h-[10px] w-[10px] rounded-full"
            style={{
              border: `1.6px solid ${i.color}`,
              background: "transparent",
            }}
          />
          {i.label}
        </span>
      ))}
    </div>
  );
}
