"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

export default function ProvisionPage() {
  const router = useRouter();
  const [companyName, setCompanyName] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [domain, setDomain] = useState("");
  const [industry, setIndustry] = useState("");
  const [plan, setPlan] = useState("trial");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [inviteUrl, setInviteUrl] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setInviteUrl("");
    setLoading(true);

    try {
      const supabase = createClient();
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) throw new Error("Not authenticated");

      const result = await apiFetch<{
        client_id: string;
        invite_url: string;
      }>("/api/admin/provision-client", {
        method: "POST",
        token,
        body: JSON.stringify({
          client_name: companyName,
          admin_email: adminEmail,
          domain,
          industry,
          plan,
        }),
      });

      if (result.invite_url) {
        setInviteUrl(result.invite_url);
      }

      router.push("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Provisioning failed");
      setLoading(false);
    }
  }

  return (
    <>
      <div className="mb-6">
        <h1 className="text-lg font-semibold text-zinc-900">
          Provision New Client
        </h1>
        <p className="text-sm text-zinc-500 mt-1">
          Creates the company and sends an invite to the designated admin.
        </p>
      </div>

      <form
        onSubmit={handleSubmit}
        className="max-w-lg bg-white border border-zinc-200 rounded-xl p-7 space-y-4"
      >
        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">
            {error}
          </p>
        )}
        {inviteUrl && (
          <div className="text-sm bg-green-50 border border-green-200 rounded-lg p-3">
            <p className="font-medium text-green-800 mb-1">
              Invite URL (dry-run mode):
            </p>
            <code className="text-xs break-all text-green-700">{inviteUrl}</code>
          </div>
        )}
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">
            Company Name *
          </label>
          <input
            type="text"
            required
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">
            Admin Email *
          </label>
          <input
            type="email"
            required
            value={adminEmail}
            onChange={(e) => setAdminEmail(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900"
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-zinc-600 mb-1.5">
              Domain
            </label>
            <input
              type="text"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900"
              placeholder="accenture.com"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-zinc-600 mb-1.5">
              Industry
            </label>
            <input
              type="text"
              value={industry}
              onChange={(e) => setIndustry(e.target.value)}
              className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900"
              placeholder="Consulting"
            />
          </div>
        </div>
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">
            Plan
          </label>
          <select
            value={plan}
            onChange={(e) => setPlan(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900 bg-white"
          >
            <option value="trial">Trial</option>
            <option value="pro">Pro</option>
            <option value="enterprise">Enterprise</option>
          </select>
        </div>
        <div className="flex gap-3 justify-end pt-2">
          <button
            type="button"
            onClick={() => router.push("/dashboard")}
            className="px-5 py-2.5 border border-zinc-200 rounded-lg text-sm text-zinc-600 hover:bg-zinc-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={loading}
            className="px-5 py-2.5 bg-zinc-900 text-white rounded-lg text-sm font-medium hover:bg-zinc-800 disabled:opacity-50"
          >
            {loading ? "Provisioning..." : "Provision & Send Invite"}
          </button>
        </div>
      </form>
    </>
  );
}
