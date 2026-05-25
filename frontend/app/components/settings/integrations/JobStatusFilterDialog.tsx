"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Skeleton,
} from "@/components/px";
import {
  listJobStatuses,
  triggerManualSync,
  updateJobStatusFilter,
  type CeipalJobStatus,
  type JobStatusFilter,
} from "@/lib/api/ats";
import { ApiError } from "@/lib/api/client";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";
import { brand } from "@/lib/brand";

type Props = {
  open: boolean;
  onClose: () => void;
  connectionId: string;
  priorFilter: JobStatusFilter | null;
  /**
   * If true, the dialog triggers the single-trigger sync after saving the
   * filter. The /jobs page uses this for the first-time configuration
   * flow; the connections detail page passes false and the recruiter
   * triggers the sync via its own "Resync" button.
   */
  triggerSyncOnSave?: boolean;
  /**
   * Called after the trigger-sync mutation resolves successfully (only
   * when triggerSyncOnSave=true). The /jobs page uses this to open its
   * progress popup *optimistically* — the next sync-logs poll happens at
   * the 10s idle interval, so without this hook the popup wouldn't appear
   * until the user reloads.
   */
  onSyncTriggered?: () => void;
};

export function JobStatusFilterDialog({
  open,
  onClose,
  connectionId,
  priorFilter,
  triggerSyncOnSave = false,
  onSyncTriggered,
}: Props) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [serverError, setServerError] = useState<string | null>(null);

  const statuses = useQuery<CeipalJobStatus[]>({
    queryKey: ["ats", "connection", connectionId, "job-statuses"],
    queryFn: async () =>
      listJobStatuses(await getFreshSupabaseToken(), connectionId),
    enabled: open,
    staleTime: 0,
  });

  // Initialize selection when statuses load. Restore prior filter if
  // present; otherwise auto-pick "Active" (matched by name).
  useEffect(() => {
    if (!statuses.data) return;
    if (priorFilter) {
      setSelected(new Set(priorFilter.ids));
      return;
    }
    const active = statuses.data.find((s) => s.name === "Active");
    setSelected(new Set(active ? [active.id] : []));
  }, [statuses.data, priorFilter]);

  const mutation = useMutation({
    mutationFn: async (body: JobStatusFilter) => {
      const token = await getFreshSupabaseToken();
      await updateJobStatusFilter(token, connectionId, body);
      // Optionally kick off a sync once the filter is in place. The new
      // sync is single-trigger — no `phases` argument. Subsequent runs
      // are recruiter-driven via the Resync button.
      if (triggerSyncOnSave) {
        await triggerManualSync(token, connectionId);
      }
    },
    onSuccess: () => {
      toast.success(
        triggerSyncOnSave ? "Filter saved. Syncing…" : "Filter saved.",
      );
      qc.invalidateQueries({ queryKey: ["ats", "connection", connectionId] });
      qc.invalidateQueries({
        queryKey: ["ats", "connection", connectionId, "sync-logs"],
      });
      if (triggerSyncOnSave) {
        onSyncTriggered?.();
      }
      onClose();
    },
    onError: (err) => {
      if (
        err instanceof ApiError &&
        err.status === 422 &&
        err.code === "JOB_STATUS_FILTER_INVALID"
      ) {
        setServerError("Filter rejected by the server. Pick at least one.");
        return;
      }
      toast.error("Could not save filter. Please try again.");
    },
  });

  const orderedSelection = useMemo(() => {
    if (!statuses.data) return { ids: [] as number[], names: [] as string[] };
    const ids: number[] = [];
    const names: string[] = [];
    for (const s of statuses.data) {
      if (selected.has(s.id)) {
        ids.push(s.id);
        names.push(s.name);
      }
    }
    return { ids, names };
  }, [selected, statuses.data]);

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setServerError(null);
  };

  const onSubmit = () => {
    if (orderedSelection.ids.length === 0) return;
    mutation.mutate(orderedSelection);
  };

  const noSelection = orderedSelection.ids.length === 0;

  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Which job statuses should {brand.shortName} import?</DialogTitle>
          <DialogDescription>
            Ceipal lets you pre-filter by status. Pick the ones worth syncing —
            inactive statuses cost up to 22 minutes per full sync.
          </DialogDescription>
        </DialogHeader>

        {statuses.isLoading && <Skeleton className="h-24 w-full" />}
        {statuses.isError && (
          <p className="px-hint" style={{ color: "var(--px-danger)" }}>
            Could not load statuses from Ceipal. Check that the credentials are
            still valid.
          </p>
        )}
        {statuses.data && (
          <div className="space-y-2 py-2">
            {statuses.data.map((s) => (
              <Checkbox
                key={s.id}
                id={`status-${s.id}`}
                label={s.name}
                checked={selected.has(s.id)}
                onChange={() => toggle(s.id)}
              />
            ))}
          </div>
        )}

        {/* Spec § "JobStatusFilterDialog": block Save with an inline banner
            when no statuses are selected. Matches the backend 422 the new
            sync trigger raises (`JOB_STATUS_FILTER_EMPTY`). */}
        {!statuses.isLoading && noSelection && (
          <p
            className="px-hint mt-2 rounded-[8px] border px-3 py-2"
            style={{
              borderColor: "var(--px-warning-border, #fde68a)",
              background: "var(--px-warning-bg, #fffbeb)",
              color: "var(--px-fg)",
            }}
          >
            At least one status must be selected; the sync cannot run without
            an active filter.
          </p>
        )}

        {serverError && (
          <p className="px-hint" style={{ color: "var(--px-danger)" }}>
            {serverError}
          </p>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} type="button">
            Cancel
          </Button>
          <Button
            onClick={onSubmit}
            disabled={
              statuses.isLoading || noSelection || mutation.isPending
            }
          >
            {mutation.isPending
              ? "Saving…"
              : triggerSyncOnSave
                ? "Sync"
                : "Save filter"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
