"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Badge, Button, DangerConfirmDialog, Skeleton } from "@/components/px";
import { SyncLogTable } from "@/components/settings/integrations/SyncLogTable";
import {
  deleteConnection,
  getConnection,
  listSyncLogs,
  triggerManualSync,
  type ATSConnection,
  type ATSSyncLog,
} from "@/lib/api/ats";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";

export default function ConnectionDetailPage() {
  const params = useParams<{ connectionId: string }>();
  const connectionId = params.connectionId;
  const router = useRouter();
  const queryClient = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);

  const connection = useQuery<ATSConnection>({
    queryKey: ["ats", "connection", connectionId],
    queryFn: async () =>
      getConnection(await getFreshSupabaseToken(), connectionId),
  });

  const syncLogs = useQuery<ATSSyncLog[]>({
    queryKey: ["ats", "connection", connectionId, "sync-logs"],
    queryFn: async () =>
      listSyncLogs(await getFreshSupabaseToken(), connectionId),
    refetchInterval: 10_000, // poll for new sync logs every 10s
  });

  const syncNow = useMutation({
    mutationFn: async () =>
      triggerManualSync(await getFreshSupabaseToken(), connectionId),
    onSuccess: () => {
      toast.success("Sync queued. Logs refresh in 10 seconds.");
      queryClient.invalidateQueries({
        queryKey: ["ats", "connection", connectionId, "sync-logs"],
      });
    },
    onError: () => toast.error("Could not trigger sync."),
  });

  const remove = useMutation({
    mutationFn: async () =>
      deleteConnection(await getFreshSupabaseToken(), connectionId),
    onSuccess: () => {
      toast.success("ATS connection removed.");
      queryClient.invalidateQueries({ queryKey: ["ats", "connections"] });
      router.push("/settings/integrations");
    },
    onError: () => toast.error("Could not delete connection."),
  });

  if (connection.isLoading) {
    return (
      <div className="mx-auto max-w-[1400px] space-y-3 px-8 pb-10 pt-5">
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
  if (!connection.data) {
    return (
      <div className="mx-auto max-w-[1400px] px-8 pb-10 pt-5 text-sm text-zinc-500">
        Connection not found.
      </div>
    );
  }

  const c = connection.data;
  return (
    <div className="mx-auto max-w-[1400px] space-y-6 px-8 pb-10 pt-5">
      <div>
        <h1
          className="px-serif m-0 text-[30px] font-normal"
          style={{ letterSpacing: "-0.6px", color: "var(--px-fg)" }}
        >
          {c.vendor === "ceipal" ? "Ceipal" : c.vendor}
        </h1>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-zinc-500">
          <Badge variant={c.active ? "ok" : "danger"}>
            {c.active ? "Active" : "Disabled"}
          </Badge>
          {c.last_synced_at && (
            <span>
              Last synced {new Date(c.last_synced_at).toLocaleString()}
            </span>
          )}
          {c.disabled_reason && (
            <span style={{ color: "var(--px-danger)" }}>
              {c.disabled_reason}
            </span>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button onClick={() => syncNow.mutate()} disabled={syncNow.isPending}>
          {syncNow.isPending ? "Queueing…" : "Sync now"}
        </Button>
        <Button
          variant="outline"
          onClick={() =>
            router.push(`/settings/integrations/${connectionId}/users`)
          }
        >
          Manage user mappings
        </Button>
        <div className="ml-auto">
          <Button
            variant="destructive"
            onClick={() => setConfirmDelete(true)}
            disabled={remove.isPending}
          >
            Remove connection
          </Button>
        </div>
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-medium text-zinc-900">Recent syncs</h2>
        <SyncLogTable
          logs={syncLogs.data ?? []}
          isLoading={syncLogs.isLoading}
        />
      </section>

      <DangerConfirmDialog
        open={confirmDelete}
        title="Remove ATS connection?"
        description="This stops scheduled syncs. Imported clients, jobs, and candidates stay in ProjectX."
        confirmLabel="Remove"
        pendingLabel="Removing…"
        pending={remove.isPending}
        onConfirm={() => {
          remove.mutate(undefined, {
            onSuccess: () => setConfirmDelete(false),
          });
        }}
        onClose={() => setConfirmDelete(false)}
      />
    </div>
  );
}
