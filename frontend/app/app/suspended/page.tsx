import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { authApi } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { SignOutButton } from "./sign-out-button";

export default async function SuspendedPage() {
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session?.access_token) redirect("/login");

  // Re-verify suspension. If /me succeeds, the tenant has been unblocked
  // since the user landed here — bounce them back to the dashboard.
  try {
    await authApi.me(session.access_token);
    redirect("/");
  } catch (err) {
    if (!(err instanceof ApiError) || err.code !== "ACCOUNT_SUSPENDED") {
      throw err;
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div
        className="max-w-md w-full rounded-[12px] border p-8 text-center"
        style={{
          background: "var(--px-surface)",
          borderColor: "var(--px-hairline)",
          boxShadow: "var(--px-shadow-sm)",
        }}
      >
        <div
          className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-full"
          style={{ background: "var(--px-danger-bg)" }}
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
            style={{ color: "var(--px-danger)" }}
            aria-hidden="true"
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="4.93" y1="4.93" x2="19.07" y2="19.07" />
          </svg>
        </div>
        <h1
          className="px-serif m-0 text-[28px] font-normal"
          style={{ letterSpacing: "-0.6px", color: "var(--px-fg)" }}
        >
          Account suspended
        </h1>
        <p className="mt-3 text-sm" style={{ color: "var(--px-fg-3)" }}>
          Your organization&apos;s ProjectX workspace has been suspended.
          Please contact your administrator if you believe this is an error.
        </p>
        <div className="mt-6">
          <SignOutButton />
        </div>
      </div>
    </div>
  );
}
