"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

interface Client {
  client_id: string;
  client_name: string;
  domain: string | null;
  plan: string;
  onboarding_complete: boolean;
  admin_email: string | null;
  invite_status: string | null;
  created_at: string;
}

export default function DashboardPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      try {
        const supabase = createClient();
        const {
          data: { session },
        } = await supabase.auth.getSession();
        const token = session?.access_token;
        if (!token) {
          window.location.href = "/login";
          return;
        }

        const data = await apiFetch<Client[]>("/api/admin/clients", { token });
        setClients(data);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to load clients",
        );
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const statusColor: Record<string, string> = {
    pending: "bg-amber-50 text-amber-700",
    accepted: "bg-green-50 text-green-700",
    expired: "bg-zinc-100 text-zinc-500",
    revoked: "bg-red-50 text-red-600",
  };

  return (
    <>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-lg font-semibold text-zinc-900">Clients</h1>
        <Link
          href="/dashboard/provision"
          className="bg-zinc-900 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-zinc-800"
        >
          + Provision Client
        </Link>
      </div>

      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3 mb-4">
          {error}
        </p>
      )}

      {loading ? (
        <p className="text-sm text-zinc-500">Loading...</p>
      ) : clients.length === 0 ? (
        <p className="text-sm text-zinc-500">No clients provisioned yet.</p>
      ) : (
        <div className="bg-white border border-zinc-200 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-zinc-50 border-b border-zinc-200">
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">
                  Company
                </th>
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">
                  Admin Email
                </th>
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">
                  Plan
                </th>
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">
                  Invite
                </th>
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">
                  Created
                </th>
              </tr>
            </thead>
            <tbody>
              {clients.map((c) => (
                <tr
                  key={c.client_id}
                  className="border-b border-zinc-100 last:border-0"
                >
                  <td className="px-4 py-2.5 font-medium text-zinc-900">
                    {c.client_name}
                  </td>
                  <td className="px-4 py-2.5 text-zinc-600">
                    {c.admin_email || "\u2014"}
                  </td>
                  <td className="px-4 py-2.5">
                    <span className="bg-green-50 text-green-700 px-2 py-0.5 rounded-full text-xs">
                      {c.plan}
                    </span>
                  </td>
                  <td className="px-4 py-2.5">
                    {c.invite_status ? (
                      <span
                        className={`px-2 py-0.5 rounded-full text-xs ${statusColor[c.invite_status] || "bg-zinc-100 text-zinc-500"}`}
                      >
                        {c.invite_status}
                      </span>
                    ) : (
                      "\u2014"
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-zinc-400">
                    {new Date(c.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
