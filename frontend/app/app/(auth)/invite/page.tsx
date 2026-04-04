"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

interface InviteDetails {
  email: string;
  client_name: string;
}

function InviteContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const rawToken = searchParams.get("token") || "";

  const [state, setState] = useState<
    "loading" | "invalid" | "ready" | "submitting"
  >("loading");
  const [invite, setInvite] = useState<InviteDetails | null>(null);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!rawToken) {
      setState("invalid");
      return;
    }
    apiFetch<InviteDetails>(
      `/api/auth/verify-invite?token=${encodeURIComponent(rawToken)}`,
    )
      .then((data) => {
        setInvite(data);
        setState("ready");
      })
      .catch(() => setState("invalid"));
  }, [rawToken]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }

    setState("submitting");

    try {
      const supabase = createClient();

      // Try signup first; fall back to signIn if user already exists
      let session;
      const { data: suData, error: signUpError } =
        await supabase.auth.signUp({
          email: invite!.email,
          password,
        });

      if (signUpError) {
        if (
          signUpError.message.toLowerCase().includes("already registered")
        ) {
          const { data: signInData, error: signInError } =
            await supabase.auth.signInWithPassword({
              email: invite!.email,
              password,
            });
          if (signInError) throw new Error(signInError.message);
          session = signInData.session;
        } else {
          throw new Error(signUpError.message);
        }
      } else {
        session = suData.session;
      }

      const token = session?.access_token;
      if (!token) throw new Error("No session after signup");

      const result = await apiFetch<{ redirect_to: string }>(
        "/api/auth/complete-invite",
        {
          method: "POST",
          token,
          body: JSON.stringify({ raw_token: rawToken }),
        },
      );

      router.push(result.redirect_to);
      router.refresh();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to create account",
      );
      setState("ready");
    }
  }

  if (state === "loading") {
    return (
      <div className="text-center py-20">
        <p className="text-sm text-zinc-500">Verifying your invite...</p>
      </div>
    );
  }

  if (state === "invalid") {
    return (
      <div className="text-center py-20">
        <div className="w-12 h-12 rounded-full bg-red-50 flex items-center justify-center mx-auto mb-4">
          <span className="text-red-500 text-xl">&#x2715;</span>
        </div>
        <h2 className="text-lg font-semibold text-zinc-900 mb-2">
          Invite Invalid or Expired
        </h2>
        <p className="text-sm text-zinc-500 max-w-sm mx-auto leading-relaxed">
          This invite link is no longer valid. It may have already been used
          or expired. Please contact the person who invited you to request a
          new one.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="text-center mb-6">
        <h1 className="text-xl font-bold text-zinc-900">
          Set Up Your Account
        </h1>
        <p className="text-sm text-zinc-500 mt-1">
          You&apos;ve been invited to join
        </p>
      </div>

      <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-center mb-6">
        <p className="font-semibold text-green-800">
          {invite!.client_name}
        </p>
      </div>

      <form
        onSubmit={handleSubmit}
        className="bg-white border border-zinc-200 rounded-xl p-7 space-y-4"
      >
        {error && (
          <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">
            {error}
          </p>
        )}
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">
            Email
          </label>
          <div className="flex items-center justify-between border border-zinc-200 rounded-lg px-3 py-2.5 bg-zinc-50 text-sm text-zinc-500">
            {invite!.email}
            <span className="text-xs bg-zinc-200 text-zinc-500 px-2 py-0.5 rounded">
              locked
            </span>
          </div>
        </div>
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">
            Set Password
          </label>
          <input
            type="password"
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-green-600"
            placeholder="Minimum 8 characters"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">
            Confirm Password
          </label>
          <input
            type="password"
            required
            minLength={8}
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-green-600"
          />
        </div>
        <button
          type="submit"
          disabled={state === "submitting"}
          className="w-full bg-green-600 text-white rounded-lg py-2.5 text-sm font-medium hover:bg-green-700 disabled:opacity-50"
        >
          {state === "submitting"
            ? "Creating account..."
            : "Create Account & Continue"}
        </button>
      </form>
    </>
  );
}

export default function InvitePage() {
  return (
    <Suspense
      fallback={
        <div className="text-center py-20">
          <p className="text-sm text-zinc-500">Loading...</p>
        </div>
      }
    >
      <InviteContent />
    </Suspense>
  );
}
