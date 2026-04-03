"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

export default function OnboardingPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleComplete() {
    setLoading(true);
    setError("");

    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) {
        router.push("/login");
        return;
      }

      await apiFetch("/api/auth/onboarding/complete", {
        method: "POST",
        token,
      });

      router.push("/");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to complete onboarding");
      setLoading(false);
    }
  }

  return (
    <div className="text-center max-w-md">
      <div className="w-12 h-12 rounded-full bg-green-50 flex items-center justify-center mx-auto mb-4">
        <span className="text-green-600 text-xl">&#x2713;</span>
      </div>
      <h1 className="text-xl font-semibold text-zinc-900 mb-2">
        Welcome to ProjectX
      </h1>
      <p className="text-sm text-zinc-500 leading-relaxed mb-6">
        Your account has been created successfully. The onboarding wizard is
        coming soon — for now, click below to start using the dashboard.
      </p>
      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3 mb-4">
          {error}
        </p>
      )}
      <button
        onClick={handleComplete}
        disabled={loading}
        className="bg-green-600 text-white px-6 py-2.5 rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50"
      >
        {loading ? "Setting up..." : "Go to Dashboard"}
      </button>
    </div>
  );
}
