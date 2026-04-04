"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

const UNIT_TYPES = [
  { value: "client_account", label: "Client Account" },
  { value: "department", label: "Department" },
  { value: "team", label: "Team" },
  { value: "branch", label: "Branch" },
  { value: "region", label: "Region" },
];

export default function NewOrgUnitPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [unitType, setUnitType] = useState("department");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) {
        window.location.href = "/login";
        return;
      }

      await apiFetch("/api/org-units", {
        method: "POST",
        token,
        body: JSON.stringify({ name, unit_type: unitType }),
      });

      router.push("/");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create org unit");
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <div className="w-full max-w-md">
        <div className="text-center mb-6">
          <h1 className="text-xl font-semibold text-zinc-900">Create Your First Organization</h1>
          <p className="text-sm text-zinc-500 mt-2 leading-relaxed">
            Before you can invite team members, you need to create at least one
            organizational unit to structure your workspace.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="bg-white border border-zinc-200 rounded-xl p-7 space-y-4">
          {error && (
            <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">{error}</p>
          )}
          <div>
            <label className="block text-xs font-medium text-zinc-600 mb-1.5">Organization Name</label>
            <input
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-green-600"
              placeholder="e.g., Engineering, NYC Office, Accenture Account"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-zinc-600 mb-1.5">Type</label>
            <select
              value={unitType}
              onChange={(e) => setUnitType(e.target.value)}
              className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 bg-white"
            >
              {UNIT_TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full bg-green-600 text-white rounded-lg py-2.5 text-sm font-medium hover:bg-green-700 disabled:opacity-50"
          >
            {loading ? "Creating..." : "Create Organization"}
          </button>
        </form>
      </div>
    </div>
  );
}
