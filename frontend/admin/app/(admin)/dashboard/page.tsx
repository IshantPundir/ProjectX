"use client";

import { useEffect, useRef, useState } from "react";
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
  status: "active" | "blocked" | "deleted";
  blocked_at: string | null;
  deleted_at: string | null;
}

type StatusUpdate = Pick<Client, "client_id" | "status" | "blocked_at" | "deleted_at">;

async function getToken(): Promise<string | null> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  return session?.access_token ?? null;
}

export default function DashboardPage() {
  const [clients, setClients] = useState<Client[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Client | null>(null);
  const [confirmHardDelete, setConfirmHardDelete] = useState<Client | null>(null);
  const [hardDeleteInput, setHardDeleteInput] = useState("");
  const [hardDeleteSubmitting, setHardDeleteSubmitting] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const token = await getToken();
        if (!token) {
          window.location.href = "/login";
          return;
        }
        const data = await apiFetch<Client[]>("/api/admin/clients", { token });
        setClients(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load clients");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  // Close action menu on outside click
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!openMenu) return;
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpenMenu(null);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [openMenu]);

  function applyUpdate(update: StatusUpdate) {
    setClients((prev) =>
      prev.map((c) =>
        c.client_id === update.client_id
          ? { ...c, status: update.status, blocked_at: update.blocked_at, deleted_at: update.deleted_at }
          : c,
      ),
    );
  }

  async function runAction(client: Client, action: "block" | "unblock" | "delete") {
    setError("");
    setPending(client.client_id);
    setOpenMenu(null);
    try {
      const token = await getToken();
      if (!token) {
        window.location.href = "/login";
        return;
      }
      const path =
        action === "delete"
          ? `/api/admin/clients/${client.client_id}`
          : `/api/admin/clients/${client.client_id}/${action}`;
      const result = await apiFetch<StatusUpdate>(path, {
        token,
        method: action === "delete" ? "DELETE" : "POST",
      });
      applyUpdate(result);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : `Failed to ${action} client`,
      );
    } finally {
      setPending(null);
      setConfirmDelete(null);
    }
  }

  const inviteStatusColor: Record<string, string> = {
    pending: "bg-amber-50 text-amber-700",
    accepted: "bg-green-50 text-green-700",
    expired: "bg-zinc-100 text-zinc-500",
    revoked: "bg-red-50 text-red-600",
  };

  const lifecycleColor: Record<Client["status"], string> = {
    active: "bg-green-50 text-green-700",
    blocked: "bg-orange-50 text-orange-700",
    deleted: "bg-red-50 text-red-700",
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
        <div className="bg-white border border-zinc-200 rounded-lg overflow-visible">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-zinc-50 border-b border-zinc-200">
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Company</th>
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Admin Email</th>
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Plan</th>
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Invite</th>
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Status</th>
                <th className="text-left px-4 py-2.5 font-medium text-zinc-500">Created</th>
                <th className="px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody>
              {clients.map((c) => {
                const isPending = pending === c.client_id;
                const isDeleted = c.status === "deleted";
                const isBlocked = c.status === "blocked";
                const menuOpen = openMenu === c.client_id;
                return (
                  <tr key={c.client_id} className="border-b border-zinc-100 last:border-0">
                    <td className="px-4 py-2.5 font-medium text-zinc-900">{c.client_name}</td>
                    <td className="px-4 py-2.5 text-zinc-600">{c.admin_email || "—"}</td>
                    <td className="px-4 py-2.5">
                      <span className="bg-green-50 text-green-700 px-2 py-0.5 rounded-full text-xs">
                        {c.plan}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      {c.invite_status ? (
                        <span
                          className={`px-2 py-0.5 rounded-full text-xs ${inviteStatusColor[c.invite_status] || "bg-zinc-100 text-zinc-500"}`}
                        >
                          {c.invite_status}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <span
                        className={`px-2 py-0.5 rounded-full text-xs ${lifecycleColor[c.status]}`}
                      >
                        {c.status}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-zinc-400">
                      {new Date(c.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-4 py-2.5 text-right relative">
                      <button
                        type="button"
                        disabled={isPending}
                        onClick={() => setOpenMenu(menuOpen ? null : c.client_id)}
                        className="text-zinc-400 hover:text-zinc-700 disabled:opacity-40 disabled:cursor-not-allowed px-2 py-1 rounded"
                        aria-haspopup="menu"
                        aria-expanded={menuOpen}
                        aria-label={`Actions for ${c.client_name}`}
                      >
                        {isPending ? "..." : "⋯"}
                      </button>
                      {menuOpen && (
                        <div
                          ref={menuRef}
                          role="menu"
                          className="absolute right-4 top-10 z-10 bg-white border border-zinc-200 rounded-lg shadow-md w-44 py-1 text-left"
                        >
                          {isDeleted ? (
                            <button
                              type="button"
                              role="menuitem"
                              onClick={() => {
                                setOpenMenu(null);
                                setConfirmHardDelete(c);
                              }}
                              className="w-full text-left px-3 py-2 text-sm text-red-600 hover:bg-red-50"
                            >
                              Permanently delete
                            </button>
                          ) : (
                            <>
                              {isBlocked ? (
                                <button
                                  type="button"
                                  role="menuitem"
                                  onClick={() => runAction(c, "unblock")}
                                  className="w-full text-left px-3 py-2 text-sm hover:bg-zinc-50"
                                >
                                  Unblock
                                </button>
                              ) : (
                                <button
                                  type="button"
                                  role="menuitem"
                                  onClick={() => runAction(c, "block")}
                                  className="w-full text-left px-3 py-2 text-sm hover:bg-zinc-50"
                                >
                                  Block
                                </button>
                              )}
                              <button
                                type="button"
                                role="menuitem"
                                onClick={() => {
                                  setOpenMenu(null);
                                  setConfirmDelete(c);
                                }}
                                className="w-full text-left px-3 py-2 text-sm text-red-600 hover:bg-red-50"
                              >
                                Delete
                              </button>
                            </>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {confirmDelete && (
        <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6">
            <h2 className="text-base font-semibold text-zinc-900 mb-2">Delete client?</h2>
            <p className="text-sm text-zinc-600 mb-4">
              This will mark <strong>{confirmDelete.client_name}</strong> as deleted.
              All users in this tenant will be locked out immediately. Restoring is
              only possible by editing the database directly.
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmDelete(null)}
                disabled={pending === confirmDelete.client_id}
                className="px-4 py-2 text-sm rounded-lg border border-zinc-200 hover:bg-zinc-50 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => runAction(confirmDelete, "delete")}
                disabled={pending === confirmDelete.client_id}
                className="px-4 py-2 text-sm rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
              >
                {pending === confirmDelete.client_id ? "Deleting..." : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
