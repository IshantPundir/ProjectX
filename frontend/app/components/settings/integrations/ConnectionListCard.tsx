"use client";

import Link from "next/link";

import { Badge, Button } from "@/components/px";
import type { ATSConnection } from "@/lib/api/ats";

const VENDOR_LABEL: Record<string, string> = { ceipal: "Ceipal" };

export function ConnectionListCard({
  connection,
}: {
  connection: ATSConnection;
}) {
  const statusBadge = !connection.active ? (
    <Badge variant="danger">Disabled</Badge>
  ) : connection.last_poll_error ? (
    <Badge variant="caution">Error</Badge>
  ) : (
    <Badge variant="ok">Active</Badge>
  );

  const lastSynced = connection.last_synced_at
    ? new Date(connection.last_synced_at).toLocaleString()
    : "Never";
  const nextPoll = connection.next_poll_at
    ? new Date(connection.next_poll_at).toLocaleString()
    : "—";

  return (
    <div
      className="flex items-center justify-between rounded-[10px] border p-4"
      style={{
        background: "var(--px-surface)",
        borderColor: "var(--px-hairline)",
      }}
    >
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <h3 className="font-medium text-zinc-900">
            {VENDOR_LABEL[connection.vendor] ?? connection.vendor}
          </h3>
          {statusBadge}
        </div>
        <p className="text-sm text-zinc-500">
          Last synced: {lastSynced} · Next: {nextPoll}
        </p>
        {connection.disabled_reason && (
          <p className="text-sm" style={{ color: "var(--px-danger)" }}>
            {connection.disabled_reason}
          </p>
        )}
      </div>
      <Link href={`/settings/integrations/${connection.id}`}>
        <Button variant="outline" size="sm">
          Manage
        </Button>
      </Link>
    </div>
  );
}
