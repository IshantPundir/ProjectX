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
  updateJobStatusFilter,
  type CeipalJobStatus,
  type JobStatusFilter,
} from "@/lib/api/ats";
import { ApiError } from "@/lib/api/client";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";

type Props = {
  open: boolean;
  onClose: () => void;
  connectionId: string;
  priorFilter: JobStatusFilter | null;
};

export function JobStatusFilterDialog({
  open,
  onClose,
  connectionId,
  priorFilter,
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

  // Initialize selection when statuses load. Restore prior filter if present;
  // otherwise auto-pick "Active" (matched by name).
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
    mutationFn: async (body: JobStatusFilter) =>
      updateJobStatusFilter(await getFreshSupabaseToken(), connectionId, body),
    onSuccess: () => {
      toast.success("Filter saved. Jobs sync started.");
      qc.invalidateQueries({ queryKey: ["ats", "connection", connectionId] });
      qc.invalidateQueries({
        queryKey: ["ats", "connection", connectionId, "sync-logs"],
      });
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

  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Which job statuses should ProjectX import?</DialogTitle>
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
              statuses.isLoading ||
              orderedSelection.ids.length === 0 ||
              mutation.isPending
            }
          >
            {mutation.isPending ? "Saving…" : "Save & start sync"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
