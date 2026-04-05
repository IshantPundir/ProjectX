"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

function EyeIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
      <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
      <line x1="1" y1="1" x2="23" y2="23" />
    </svg>
  );
}

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
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);

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
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="text-red-500"
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
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
        <div className="w-12 h-12 rounded-full bg-green-600 flex items-center justify-center mx-auto mb-4">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="white"
            stroke="white"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polygon points="5 3 19 12 5 21 5 3" />
          </svg>
        </div>
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
          <div className="relative">
            <input
              type={showPassword ? "text" : "password"}
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-green-600"
              placeholder="Enter a password"
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              className="absolute inset-y-0 right-0 flex items-center px-3 text-zinc-400 hover:text-zinc-600"
              aria-label={showPassword ? "Hide password" : "Show password"}
            >
              {showPassword ? <EyeOffIcon /> : <EyeIcon />}
            </button>
          </div>
          <p className="text-xs text-zinc-400 mt-1">Minimum 8 characters</p>
        </div>
        <div>
          <label className="block text-xs font-medium text-zinc-600 mb-1.5">
            Confirm Password
          </label>
          <div className="relative">
            <input
              type={showConfirmPassword ? "text" : "password"}
              required
              minLength={8}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
              className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-green-600"
            />
            <button
              type="button"
              onClick={() => setShowConfirmPassword((v) => !v)}
              className="absolute inset-y-0 right-0 flex items-center px-3 text-zinc-400 hover:text-zinc-600"
              aria-label={showConfirmPassword ? "Hide password" : "Show password"}
            >
              {showConfirmPassword ? <EyeOffIcon /> : <EyeIcon />}
            </button>
          </div>
        </div>
        <button
          type="submit"
          disabled={state === "submitting"}
          className="w-full bg-green-600 text-white rounded-lg py-2.5 text-sm font-medium hover:bg-green-700 disabled:opacity-50 transition-colors duration-150"
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
