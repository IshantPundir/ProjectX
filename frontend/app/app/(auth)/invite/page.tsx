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

      // Guard against open-redirect — the backend-returned `redirect_to`
      // must be a same-origin relative path. The only legitimate values
      // today are `/` and `/onboarding`; a malicious/MITM'd response
      // sending `https://evil.com` or `//evil.com` would otherwise
      // navigate a freshly-authenticated user off-site.
      const safeRedirect = result.redirect_to?.startsWith("/") &&
        !result.redirect_to.startsWith("//")
        ? result.redirect_to
        : "/";
      router.push(safeRedirect);
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
      <div className="py-20 text-center">
        <p className="text-sm" style={{ color: 'var(--px-fg-3)' }}>
          Verifying your invite…
        </p>
      </div>
    );
  }

  if (state === "invalid") {
    return (
      <div className="py-20 text-center">
        <div
          className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full"
          style={{ background: 'var(--px-danger-bg)' }}
        >
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
            style={{ color: 'var(--px-danger)' }}
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </div>
        <h2
          className="px-serif m-0 mb-2 text-[24px] font-normal"
          style={{ color: 'var(--px-fg)' }}
        >
          Invite invalid or expired
        </h2>
        <p
          className="mx-auto max-w-sm text-sm leading-relaxed"
          style={{ color: 'var(--px-fg-3)' }}
        >
          This invite link is no longer valid. It may have already been used
          or expired. Please contact the person who invited you to request a
          new one.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="mb-6 text-center">
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
          className="px-serif m-0 text-[28px] font-normal"
          style={{ letterSpacing: '-0.5px', color: 'var(--px-fg)' }}
        >
          Set up your account
        </h1>
        <p className="mt-1 text-[13px]" style={{ color: 'var(--px-fg-3)' }}>
          You&apos;ve been invited to join
        </p>
      </div>

      <div
        className="mb-6 rounded-md border p-4 text-center"
        style={{
          background: 'var(--px-accent-tint)',
          borderColor: 'var(--px-accent-line)',
        }}
      >
        <p className="font-semibold" style={{ color: 'var(--px-accent-2)' }}>
          {invite!.client_name}
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
          <div
            className="flex items-center justify-between rounded-md border px-3 py-2 text-[13px]"
            style={{
              background: 'var(--px-surface-2)',
              borderColor: 'var(--px-hairline-strong)',
              color: 'var(--px-fg-3)',
            }}
          >
            {invite!.email}
            <span className="px-chip soft" style={{ height: 18, padding: '0 7px', fontSize: 10.5 }}>
              locked
            </span>
          </div>
        </div>
        <div>
          <label className="px-label">Set password</label>
          <div className="relative">
            <input
              type={showPassword ? "text" : "password"}
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              className="px-input pr-10"
              placeholder="Enter a password"
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              className="absolute inset-y-0 right-0 flex items-center px-3"
              style={{ color: 'var(--px-fg-4)' }}
              aria-label={showPassword ? "Hide password" : "Show password"}
            >
              {showPassword ? <EyeOffIcon /> : <EyeIcon />}
            </button>
          </div>
          <p className="px-hint">Minimum 8 characters</p>
        </div>
        <div>
          <label className="px-label">Confirm password</label>
          <div className="relative">
            <input
              type={showConfirmPassword ? "text" : "password"}
              required
              minLength={8}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
              className="px-input pr-10"
            />
            <button
              type="button"
              onClick={() => setShowConfirmPassword((v) => !v)}
              className="absolute inset-y-0 right-0 flex items-center px-3"
              style={{ color: 'var(--px-fg-4)' }}
              aria-label={showConfirmPassword ? "Hide password" : "Show password"}
            >
              {showConfirmPassword ? <EyeOffIcon /> : <EyeIcon />}
            </button>
          </div>
        </div>
        <button
          type="submit"
          disabled={state === "submitting"}
          className="px-btn primary lg w-full"
        >
          {state === "submitting"
            ? "Creating account…"
            : "Create account & continue →"}
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
