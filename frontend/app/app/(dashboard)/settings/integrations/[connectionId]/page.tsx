"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge, Button, DangerConfirmDialog, Skeleton } from "@/components/px";
import { JobStatusFilterDialog } from "@/components/settings/integrations/JobStatusFilterDialog";
import { SyncLogTable } from "@/components/settings/integrations/SyncLogTable";
import {
  deleteConnection,
  getConnection,
  listSyncLogs,
  triggerManualSync,
  type ATSConnection,
  type ATSSyncLog,
  type ATSSyncPhase,
} from "@/lib/api/ats";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";

export default function ConnectionDetailPage() {
  const params = useParams<{ connectionId: string }>();
  const connectionId = params.connectionId;
  const router = useRouter();
  const queryClient = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
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

  // One mutation, parameterised by phase scope. Passing ``undefined`` runs
  // all five phases (Sync all); passing a single-element array scopes to
  // one phase (per-phase dev controls). The button row below renders one
  // button per phase plus a "Sync all".
  const syncMutation = useMutation({
    mutationFn: async (phases: ATSSyncPhase[] | undefined) =>
      triggerManualSync(
        await getFreshSupabaseToken(),
        connectionId,
        phases,
      ),
    onSuccess: (data) => {
      const label =
        data.phases && data.phases.length > 0
          ? data.phases.join(", ")
          : "all phases";
      toast.success(`Sync queued (${label}).`);
      queryClient.invalidateQueries({
        queryKey: ["ats", "connection", connectionId, "sync-logs"],
      });
    },
    onError: () => toast.error("Could not trigger sync."),
  });

  // Track which phase button is currently spinning so we can disable just
  // that one (or "all") while the request is in flight.
  const [pendingPhase, setPendingPhase] = useState<
    ATSSyncPhase | "all" | null
  >(null);
  const triggerPhaseSync = (phases: ATSSyncPhase[] | undefined) => {
    setPendingPhase(phases && phases.length === 1 ? phases[0] : "all");
    syncMutation.mutate(phases, {
      onSettled: () => setPendingPhase(null),
    });
  };

  const PHASE_BUTTONS: { phase: ATSSyncPhase; label: string }[] = [
    { phase: "clients", label: "Sync clients" },
    { phase: "users", label: "Sync users" },
    { phase: "jobs", label: "Sync jobs" },
    { phase: "applicants", label: "Sync candidates" },
    { phase: "submissions", label: "Sync submissions" },
  ];

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

      {c.job_status_filter === null && (
        <div
          className="rounded-[10px] border p-4 text-sm bg-amber-50 border-amber-300"
          style={{ color: "var(--px-fg)" }}
        >
          <p className="font-medium">
            Configure which Ceipal job statuses to import.
          </p>
          <p className="mt-1 text-zinc-600">
            The jobs sync is paused until you pick at least one status.
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
            Trigger one phase at a time, or run all five. Each phase enqueues
            its own Dramatiq job — fine-grained control for development.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {PHASE_BUTTONS.map(({ phase, label }) => (
            <Button
              key={phase}
              variant="outline"
              onClick={() => triggerPhaseSync([phase])}
              disabled={syncMutation.isPending}
            >
              {pendingPhase === phase ? "Queueing…" : label}
            </Button>
          ))}
          <Button
            onClick={() => triggerPhaseSync(undefined)}
            disabled={syncMutation.isPending}
          >
            {pendingPhase === "all" ? "Queueing…" : "Sync all"}
          </Button>
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

      <JobStatusFilterDialog
        open={filterDialogOpen}
        onClose={() => setFilterDialogOpen(false)}
        connectionId={connectionId}
        priorFilter={c.job_status_filter}
      />
    </div>
  );
}
