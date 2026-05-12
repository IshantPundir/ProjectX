"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import {
  Button,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/px";
import { mapATSUser, type ATSUnmappedUser } from "@/lib/api/ats";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";
import { useTeamMembers } from "@/lib/hooks/use-team-members";

export function UserMappingTable({
  users,
  connectionId,
}: {
  users: ATSUnmappedUser[];
  connectionId: string;
}) {
  const queryClient = useQueryClient();
  const team = useTeamMembers();
  const [selections, setSelections] = useState<Record<string, string>>({});

  const mapMutation = useMutation({
    mutationFn: async ({
      externalUserId,
      internalUserId,
    }: {
      externalUserId: string;
      internalUserId: string;
    }) =>
      mapATSUser(
        await getFreshSupabaseToken(),
        connectionId,
        externalUserId,
        { internal_user_id: internalUserId },
      ),
    onSuccess: () => {
      toast.success("User mapped.");
      queryClient.invalidateQueries({
        queryKey: ["ats", "connection", connectionId, "unmapped-users"],
      });
    },
    onError: () => toast.error("Could not map user."),
  });

  if (users.length === 0) {
    return (
      <div
        className="rounded-[10px] border border-dashed p-8 text-center text-sm text-zinc-500"
        style={{ borderColor: "var(--px-hairline)" }}
      >
        All Ceipal users are mapped.
      </div>
    );
  }

  // Only show users that aren't already mapped as candidates here — the
  // team query returns all tenant users; backend enforces uniqueness on
  // the actual map call.
  const eligibleTeamMembers = (team.data ?? []).filter(
    (m) => m.source === "user" && m.is_active,
  );

  return (
    <div className="space-y-3">
      {users.map((u) => {
        const selectedInternalId = selections[u.external_user_id] ?? "";
        return (
          <div
            key={u.external_user_id}
            className="flex flex-wrap items-center justify-between gap-3 rounded-[10px] border p-4"
            style={{
              background: "var(--px-surface)",
              borderColor: "var(--px-hairline)",
            }}
          >
            <div className="space-y-1">
              <p className="font-medium text-zinc-900">
                {u.external_user_display_name}
              </p>
              <p className="text-sm text-zinc-500">
                {u.external_user_email} · {u.external_user_role ?? "no role"}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Select
                value={selectedInternalId}
                onValueChange={(v) =>
                  setSelections((s) => ({ ...s, [u.external_user_id]: v }))
                }
              >
                <SelectTrigger className="w-[260px]">
                  <SelectValue placeholder="Pick a ProjectX user" />
                </SelectTrigger>
                <SelectContent>
                  {eligibleTeamMembers.map((m) => (
                    <SelectItem key={m.id} value={m.id}>
                      {(m.full_name ?? m.email) + " · " + m.email}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                size="sm"
                disabled={!selectedInternalId || mapMutation.isPending}
                onClick={() =>
                  mapMutation.mutate({
                    externalUserId: u.external_user_id,
                    internalUserId: selectedInternalId,
                  })
                }
              >
                Map
              </Button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
