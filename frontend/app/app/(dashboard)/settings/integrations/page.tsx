"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { Button, Skeleton } from "@/components/px";
import { ConnectionListCard } from "@/components/settings/integrations/ConnectionListCard";
import { listConnections, type ATSConnection } from "@/lib/api/ats";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";

export default function IntegrationsPage() {
  const { data, isLoading, error } = useQuery<ATSConnection[]>({
    queryKey: ["ats", "connections"],
    queryFn: async () => listConnections(await getFreshSupabaseToken()),
  });

  return (
    <div className="mx-auto max-w-[1400px] space-y-6 px-8 pb-10 pt-5">
      <div className="flex items-center justify-between">
        <div>
          <h1
            className="px-serif m-0 text-[30px] font-normal"
            style={{ letterSpacing: "-0.6px", color: "var(--px-fg)" }}
          >
            Integrations
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Connect an ATS so ProjectX can import your clients, jobs, and
            candidates automatically.
          </p>
        </div>
        <Link href="/settings/integrations/connect">
          <Button>Connect ATS</Button>
        </Link>
      </div>

      {isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      )}

      {error && (
        <div
          className="rounded-[10px] border p-4 text-sm"
          style={{
            background: "color-mix(in oklab, var(--px-danger) 8%, transparent)",
            borderColor:
              "color-mix(in oklab, var(--px-danger) 40%, transparent)",
            color: "var(--px-danger)",
          }}
        >
          Could not load integrations. {(error as Error).message}
        </div>
      )}

      {data && data.length === 0 && (
        <div
          className="rounded-[10px] border border-dashed p-8 text-center"
          style={{ borderColor: "var(--px-hairline)" }}
        >
          <p className="text-sm text-zinc-500">
            No ATS connected yet. Connect Ceipal to start importing jobs and
            candidates.
          </p>
        </div>
      )}

      {data && data.length > 0 && (
        <div className="space-y-3">
          {data.map((c) => (
            <ConnectionListCard key={c.id} connection={c} />
          ))}
        </div>
      )}
    </div>
  );
}
