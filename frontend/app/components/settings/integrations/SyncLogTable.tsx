"use client";

import { Badge, Skeleton } from "@/components/px";
import type { ATSSyncLog, ATSSyncStatus } from "@/lib/api/ats";

const STATUS_VARIANT: Record<
  ATSSyncStatus,
  "secondary" | "ok" | "caution" | "danger"
> = {
  running: "secondary",
  success: "ok",
  partial: "caution",
  failed: "danger",
};

function formatCounts(counts: ATSSyncLog["entity_counts"]): string {
  const parts: string[] = [];
  for (const [phase, c] of Object.entries(counts)) {
    if (!c) continue;
    if (c.new || c.updated) {
      parts.push(`${phase}: +${c.new ?? 0}/~${c.updated ?? 0}`);
    }
  }
  return parts.join(" · ") || "—";
}

export function SyncLogTable({
  logs,
  isLoading,
}: {
  logs: ATSSyncLog[];
  isLoading: boolean;
}) {
  if (isLoading) return <Skeleton className="h-24 w-full" />;
  if (logs.length === 0) {
    return <p className="text-sm text-zinc-500">No syncs recorded yet.</p>;
  }
  return (
    <div
      className="overflow-x-auto rounded-[10px] border"
      style={{
        background: "var(--px-surface)",
        borderColor: "var(--px-hairline)",
      }}
    >
      <table className="w-full text-sm">
        <thead className="border-b bg-zinc-50">
          <tr>
            <th className="px-3 py-2 text-left font-medium text-zinc-500">
              Started
            </th>
            <th className="px-3 py-2 text-left font-medium text-zinc-500">
              Status
            </th>
            <th className="px-3 py-2 text-left font-medium text-zinc-500">
              Counts
            </th>
            <th className="px-3 py-2 text-left font-medium text-zinc-500">
              Error
            </th>
          </tr>
        </thead>
        <tbody>
          {logs.map((log) => (
            <tr key={log.id} className="border-b last:border-b-0">
              <td className="px-3 py-2 text-zinc-700">
                {new Date(log.started_at).toLocaleString()}
              </td>
              <td className="px-3 py-2">
                <Badge variant={STATUS_VARIANT[log.status]}>{log.status}</Badge>
              </td>
              <td className="px-3 py-2 font-mono text-xs text-zinc-600">
                {formatCounts(log.entity_counts)}
              </td>
              <td
                className="px-3 py-2 text-xs"
                style={{ color: "var(--px-danger)" }}
              >
                {log.error_phase && (
                  <span className="font-medium">{log.error_phase}: </span>
                )}
                {log.error_summary}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
