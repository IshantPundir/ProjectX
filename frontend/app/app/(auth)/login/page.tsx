"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

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

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const supabase = createClient();
      const { data, error: authError } =
        await supabase.auth.signInWithPassword({ email, password });

      if (authError) {
        setError(authError.message);
        setLoading(false);
        return;
      }

      // Read access token — signInWithPassword returns the session directly
      const token = data.session?.access_token;
      if (!token) {
        setError("Sign-in succeeded but no session was returned. Please try again.");
        setLoading(false);
        return;
      }

      // Decode JWT claims to check tenant_id/app_role
      // Reject admin-only accounts that don't belong on the client dashboard
      const base64 = token
        .split(".")[1]
        .replace(/-/g, "+")
        .replace(/_/g, "/");
      const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
      const payload = JSON.parse(atob(padded));

      // Only check tenant_id — app_role can be empty (unassigned user)
      // This rejects admin-only accounts (no tenant) but allows unassigned users
      if (!payload.tenant_id) {
        await supabase.auth.signOut();
        setError(
          "This account does not have access to the client dashboard. Please use your invite link to set up your account.",
        );
        setLoading(false);
        return;
      }

      router.push("/");
      router.refresh();
    } catch {
      setError("An unexpected error occurred");
      setLoading(false);
    }
  }

  return (
    <>
      <div className="mb-8 text-center">
        <div
          className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full"
          style={{ background: 'var(--px-accent)' }}
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="20"
            height="20"
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
        <h1
          className="px-serif m-0 text-[32px] font-normal"
          style={{ letterSpacing: '-0.6px', color: 'var(--px-fg)' }}
        >
          ProjectX
        </h1>
        <p className="mt-1 text-[13px]" style={{ color: 'var(--px-fg-3)' }}>
          Sign in to your recruiting dashboard
        </p>
      </div>
      <form
        onSubmit={handleSubmit}
        className="space-y-4 rounded-[12px] border p-7"
        style={{
          background: 'var(--px-surface)',
          borderColor: 'var(--px-hairline)',
          boxShadow: 'var(--px-shadow-sm)',
        }}
      >
        {error && (
          <p
            className="rounded-md border p-3 text-[13px]"
            style={{
              color: 'var(--px-danger)',
              background: 'var(--px-danger-bg)',
              borderColor: 'var(--px-danger-line)',
            }}
          >
            {error}
          </p>
        )}
        <div>
          <label className="px-label">Email</label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            className="px-input"
            placeholder="you@company.com"
          />
        </div>
        <div>
          <label className="px-label">Password</label>
          <div className="relative">
            <input
              type={showPassword ? "text" : "password"}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              className="px-input pr-10"
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              className="absolute inset-y-0 right-0 flex cursor-pointer items-center px-3"
              style={{ color: 'var(--px-fg-4)' }}
              aria-label={showPassword ? "Hide password" : "Show password"}
            >
              {showPassword ? <EyeOffIcon /> : <EyeIcon />}
            </button>
          </div>
        </div>
        <button
          type="submit"
          disabled={loading}
          className="px-btn primary lg w-full"
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <p
        className="mt-4 text-center text-[12.5px]"
        style={{ color: 'var(--px-fg-4)' }}
      >
        Don&apos;t have an account? Contact your{' '}
        <strong
          className="font-semibold"
          style={{ color: 'var(--px-fg-3)' }}
        >
          Company Admin
        </strong>{' '}
        for an invite.
      </p>
    </>
  );
}
