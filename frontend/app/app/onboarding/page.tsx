"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";
import { authApi } from "@/lib/api/auth";
import { orgUnitsApi } from "@/lib/api/org-units";
import { applyApiErrorToForm } from "@/lib/api/errors";
import {
  CompanyProfileForm,
  type CompanyProfile,
} from "@/components/dashboard/company-profile-form";

export default function OnboardingPage() {
  const router = useRouter();

  const [rootUnitId, setRootUnitId] = useState("");
  const [profileError, setProfileError] = useState("");
  const [fetchingOrg, setFetchingOrg] = useState(true);

  async function getToken(): Promise<string | null> {
    try {
      return await getFreshSupabaseToken();
    } catch {
      router.push("/login");
      return null;
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function fetchRootUnit() {
      setFetchingOrg(true);
      try {
        const token = await getToken();
        if (!token) return;

        const units = await orgUnitsApi.list(token);
        const root = units.find((u) => u.is_root);
        if (root && !cancelled) {
          setRootUnitId(root.id);
        }
      } catch {
        // Non-fatal — profile form can still be submitted without pre-filling
      } finally {
        if (!cancelled) setFetchingOrg(false);
      }
    }

    fetchRootUnit();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmitProfile(value: CompanyProfile) {
    setProfileError("");

    const token = await getToken();
    if (!token) return;

    if (rootUnitId) {
      // Task 9 will replace this with column-level inline editing.
      // For now, map CompanyProfile fields to the new column-level sentinels.
      await orgUnitsApi.update(token, rootUnitId, {
        about: value.about, set_about: true,
        industry: value.industry, set_industry: true,
        hiring_bar: value.hiring_bar, set_hiring_bar: true,
      });
    }

    await authApi.completeOnboarding(token);

    router.push("/");
    router.refresh();
  }

  return (
    <div className="w-full max-w-2xl">
      <div className="mb-8 text-center">
        <h1
          className="px-serif m-0 text-[34px] font-normal"
          style={{ letterSpacing: '-0.8px', color: 'var(--px-fg)' }}
        >
          Tell us about your company
        </h1>
        <p
          className="mx-auto mt-2 max-w-md text-sm leading-relaxed"
          style={{ color: 'var(--px-fg-3)' }}
        >
          Four questions about your company. This takes about 2 minutes and
          significantly improves the quality of your AI-generated interview
          questions and rubrics.
        </p>
      </div>

      {fetchingOrg ? (
        <div className="flex items-center justify-center py-12">
          <svg
            className="w-6 h-6 text-zinc-400 animate-spin"
            fill="none"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          <span className="ml-3 text-sm text-zinc-500">
            Loading your organization...
          </span>
        </div>
      ) : (
        <div
          className="rounded-[12px] border p-7"
          style={{
            background: 'var(--px-surface)',
            borderColor: 'var(--px-hairline)',
            boxShadow: 'var(--px-shadow-sm)',
          }}
        >
          {profileError && (
            <p
              className="mb-6 rounded-md border p-3 text-sm"
              style={{
                color: 'var(--px-danger)',
                background: 'var(--px-danger-bg)',
                borderColor: 'var(--px-danger-line)',
              }}
            >
              {profileError}
            </p>
          )}
          <CompanyProfileForm
            onSubmit={handleSubmitProfile}
            onError={(err, form) => {
              if (
                applyApiErrorToForm(err, form, {
                  stripPrefixes: ['body', 'metadata'],
                })
              ) {
                return
              }
              setProfileError(
                err instanceof Error
                  ? err.message
                  : 'Failed to save company profile',
              )
            }}
            submitLabel="Finish Onboarding"
          />
        </div>
      )}
    </div>
  );
}
