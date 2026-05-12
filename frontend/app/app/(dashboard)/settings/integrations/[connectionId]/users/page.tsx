"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";

import { Skeleton } from "@/components/px";
import { UserMappingTable } from "@/components/settings/integrations/UserMappingTable";
import { listUnmappedUsers, type ATSUnmappedUser } from "@/lib/api/ats";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";

export default function UserMappingPage() {
  const params = useParams<{ connectionId: string }>();
  const connectionId = params.connectionId;

  const unmapped = useQuery<ATSUnmappedUser[]>({
    queryKey: ["ats", "connection", connectionId, "unmapped-users"],
    queryFn: async () =>
      listUnmappedUsers(await getFreshSupabaseToken(), connectionId),
  });

  return (
    <div className="mx-auto max-w-[1400px] space-y-6 px-8 pb-10 pt-5">
      <div>
        <h1
          className="px-serif m-0 text-[30px] font-normal"
          style={{ letterSpacing: "-0.6px", color: "var(--px-fg)" }}
        >
          User mappings
        </h1>
        <p className="mt-1 text-sm text-zinc-500">
          Map your Ceipal recruiters to ProjectX users so that
          assigned-recruiter fields on imported jobs resolve correctly.
        </p>
      </div>

      {unmapped.isLoading && <Skeleton className="h-32 w-full" />}
      {unmapped.data && (
        <UserMappingTable
          users={unmapped.data}
          connectionId={connectionId}
        />
      )}
    </div>
  );
}
