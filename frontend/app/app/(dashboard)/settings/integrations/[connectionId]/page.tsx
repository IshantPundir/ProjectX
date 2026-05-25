"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  Badge,
  Button,
  DangerConfirmDialog,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Skeleton,
} from "@/components/px";
import { JobStatusFilterDialog } from "@/components/settings/integrations/JobStatusFilterDialog";
import { SyncLogTable } from "@/components/settings/integrations/SyncLogTable";
import {
  deleteConnection,
  getConnection,
  listSyncLogs,
  resetCursor,
  triggerManualSync,
  updateStatusSyncMode,
  type ATSConnection,
  type ATSStatusSyncMode,
  type ATSSyncLog,
} from "@/lib/api/ats";
import { ApiError } from "@/lib/api/client";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";
import { brand } from "@/lib/brand";

const VENDOR_LABEL: Record<string, string> = {
  ats_ceipal: "Ceipal",
  ceipal: "Ceipal",
};

const SYNC_MODE_OPTIONS: { value: ATSStatusSyncMode; label: string; hint: string }[] = [
  {
    value: "advisory",
    label: "Advisory (default)",
    hint: "Log status changes + surface as recruiter tasks. No auto-apply.",
  },
  {
    value: "mirror",
    label: "Mirror",
    hint: "Auto-apply mapped status changes via stage-mappings. Borderline candidates always require human review.",
  },
  {
    value: "one_way",
    label: "Read-only",
    hint: "Log only. No notifications, no advisory actions.",
  },
];

export default function ConnectionDetailPage() {
  const params = useParams<{ connectionId: string }>();
  const connectionId = params.connectionId;
  const router = useRouter();
  const queryClient = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [filterDialogOpen, setFilterDialogOpen] = useState(false);
  const [dialogAutoOpened, setDialogAutoOpened] = useState(false);

  const connection = useQuery<ATSConnection>({
    queryKey: ["ats", "connection", connectionId],
    queryFn: async () =>
      getConnection(await getFreshSupabaseToken(), connectionId),
  });

  useEffect(() => {
    if (
      !dialogAutoOpened &&
      connection.data &&
      connection.data.job_status_filter === null
    ) {
      setFilterDialogOpen(true);
      setDialogAutoOpened(true);
    }
  }, [connection.data, dialogAutoOpened]);

  const syncLogs = useQuery<ATSSyncLog[]>({
    queryKey: ["ats", "connection", connectionId, "sync-logs"],
    queryFn: async () =>
      listSyncLogs(await getFreshSupabaseToken(), connectionId),
    refetchInterval: (query) =>
      query.state.data?.some((l) => l.status === "running") ? 2000 : 10000,
  });

  const syncMutation = useMutation({
    mutationFn: async () =>
      triggerManualSync(await getFreshSupabaseToken(), connectionId),
    onSuccess: () => {
      toast.success("Sync queued.");
      queryClient.invalidateQueries({
        queryKey: ["ats", "connection", connectionId, "sync-logs"],
      });
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 422) {
        toast.error("Configure the job status filter first.");
        return;
      }
      if (err instanceof ApiError && err.status === 409) {
        toast.error("A sync is already running.");
        return;
      }
      toast.error("Could not trigger sync.");
    },
  });

  const resetMutation = useMutation({
    mutationFn: async () =>
      resetCursor(
        await getFreshSupabaseToken(),
        connectionId,
        "force_full_rescan",
      ),
    onSuccess: () => {
      toast.success("Cursor cleared. The next sync will walk the full filter.");
      queryClient.invalidateQueries({
        queryKey: ["ats", "connection", connectionId],
      });
      setConfirmReset(false);
    },
    onError: () => toast.error("Could not reset cursor."),
  });

  const modeMutation = useMutation({
    mutationFn: async (mode: ATSStatusSyncMode) =>
      updateStatusSyncMode(
        await getFreshSupabaseToken(),
        connectionId,
        mode,
      ),
    onSuccess: () => {
      toast.success("Sync mode updated.");
      queryClient.invalidateQueries({
        queryKey: ["ats", "connection", connectionId],
      });
    },
    onError: () => toast.error("Could not update sync mode."),
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
          {VENDOR_LABEL[c.vendor] ?? c.vendor}
        </h1>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-zinc-500">
          <Badge variant={c.active ? "ok" : "danger"}>
            {c.active ? "Active" : "Disabled"}
          </Badge>
          <Badge variant="secondary">
            {SYNC_MODE_OPTIONS.find((o) => o.value === c.status_sync_mode)?.label ??
              c.status_sync_mode}
          </Badge>
          {c.last_synced_at ? (
            <span>
              Last synced {new Date(c.last_synced_at).toLocaleString()}
            </span>
          ) : (
            <span>Never synced</span>
          )}
          {c.disabled_reason && (
            <span style={{ color: "var(--px-danger)" }}>
              {c.disabled_reason}
            </span>
          )}
        </div>
      </div>

      {c.job_status_filter === null && (
        <div
          className="rounded-[10px] border p-4 text-sm bg-amber-50 border-amber-300"
          style={{ color: "var(--px-fg)" }}
        >
          <p className="font-medium">
            Configure which Ceipal job statuses to import.
          </p>
          <p className="mt-1 text-zinc-600">
            The sync is paused until you pick at least one status.
          </p>
          <Button
            className="mt-3"
            onClick={() => setFilterDialogOpen(true)}
          >
            Configure jobs filter
          </Button>
        </div>
      )}

      <section className="space-y-3">
        <div>
          <h2 className="text-lg font-medium text-zinc-900">Manual sync</h2>
          <p className="text-xs text-zinc-500">
            Cursor-based incremental. First sync walks the full filter; later
            syncs pick up records modified since the last successful run.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={() => syncMutation.mutate()}
            disabled={syncMutation.isPending || !c.active}
          >
            {syncMutation.isPending ? "Queueing…" : "Resync from ATS"}
          </Button>
          <Button
            variant="outline"
            onClick={() => setConfirmReset(true)}
            disabled={resetMutation.isPending}
          >
            Force full re-scan
          </Button>
        </div>
      </section>

      <section className="space-y-3">
        <div>
          <h2 className="text-lg font-medium text-zinc-900">Sync mode</h2>
          <p className="text-xs text-zinc-500">
            How {brand.shortName} reacts when a candidate&apos;s submission status changes
            in Ceipal. Advisory is the safe default.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Select
            value={c.status_sync_mode}
            onValueChange={(value) =>
              modeMutation.mutate(value as ATSStatusSyncMode)
            }
            disabled={modeMutation.isPending}
          >
            <SelectTrigger className="w-[260px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SYNC_MODE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-zinc-500">
            {SYNC_MODE_OPTIONS.find((o) => o.value === c.status_sync_mode)?.hint}
          </p>
        </div>
      </section>

      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => setFilterDialogOpen(true)}>
          {c.job_status_filter ? "Edit jobs filter" : "Configure jobs filter"}
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
        description={`Imported clients, jobs, and candidates stay in ${brand.shortName}. You can re-connect later, but cursor history is lost.`}
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

      <DangerConfirmDialog
        open={confirmReset}
        title="Force a full re-scan?"
        description="Clears the sync cursor. The next sync will pull every job matching the active filter, not just changes since last sync. Existing rows are diffed in place — nothing is deleted."
        confirmLabel="Reset cursor"
        pendingLabel="Resetting…"
        pending={resetMutation.isPending}
        onConfirm={() => resetMutation.mutate()}
        onClose={() => setConfirmReset(false)}
      />

      <JobStatusFilterDialog
        open={filterDialogOpen}
        onClose={() => setFilterDialogOpen(false)}
        connectionId={connectionId}
        priorFilter={c.job_status_filter}
      />
    </div>
  );
}
