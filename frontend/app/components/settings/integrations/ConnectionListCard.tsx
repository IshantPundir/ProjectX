"use client";

import Link from "next/link";

import { Badge, Button } from "@/components/px";
import type { ATSConnection, ATSStatusSyncMode } from "@/lib/api/ats";

const VENDOR_LABEL: Record<string, string> = {
  ats_ceipal: "Ceipal",
  // Legacy rows from the pre-cutover schema. After 0036 the canonical
  // value is `ats_ceipal`; this fallback keeps the UI rendering during
  // dev-mode cutover testing.
  ceipal: "Ceipal",
};

const SYNC_MODE_LABEL: Record<ATSStatusSyncMode, string> = {
  advisory: "Advisory",
  mirror: "Mirror",
  one_way: "Read-only",
};

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
          <Badge variant="secondary">
            {SYNC_MODE_LABEL[connection.status_sync_mode] ?? connection.status_sync_mode}
          </Badge>
        </div>
        <p className="text-sm text-zinc-500">Last synced: {lastSynced}</p>
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
