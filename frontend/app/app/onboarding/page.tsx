"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";
import {
  CompanyProfileForm,
  type CompanyProfile,
} from "@/components/dashboard/company-profile-form";

type Step = "workspace" | "company-profile";
type WorkspaceMode = "enterprise" | "agency";

interface OrgUnit {
  id: string;
  name: string;
  is_root: boolean;
}

export default function OnboardingPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("workspace");

  // Step 1 state
  const [selectedMode, setSelectedMode] = useState<WorkspaceMode | null>(null);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState("");

  // Step 2 state
  const [rootUnitId, setRootUnitId] = useState("");
  const [profileError, setProfileError] = useState("");
  const [fetchingOrg, setFetchingOrg] = useState(false);

  async function getToken(): Promise<string | null> {
    const supabase = createClient();
    const {
      data: { session },
    } = await supabase.auth.getSession();
    if (!session?.access_token) {
      router.push("/login");
      return null;
    }
    return session.access_token;
  }

  async function handleSelectWorkspace(mode: WorkspaceMode) {
    setSelectedMode(mode);
    setWorkspaceError("");
    setWorkspaceLoading(true);

    try {
      const token = await getToken();
      if (!token) return;

      await apiFetch("/api/settings/workspace", {
        method: "PATCH",
        token,
        body: JSON.stringify({ workspace_mode: mode }),
      });

      setStep("company-profile");
    } catch (err) {
      setWorkspaceError(
        err instanceof Error ? err.message : "Failed to set workspace type"
      );
      setSelectedMode(null);
    } finally {
      setWorkspaceLoading(false);
    }
  }

  // Fetch root org unit when entering step 2
  useEffect(() => {
    if (step !== "company-profile") return;

    let cancelled = false;

    async function fetchRootUnit() {
      setFetchingOrg(true);
      try {
        const token = await getToken();
        if (!token) return;

        const units = await apiFetch<OrgUnit[]>("/api/org-units", { token });
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
  }, [step]);

  async function handleSubmitProfile(value: CompanyProfile) {
    setProfileError("");

    const token = await getToken();
    if (!token) return;

    if (rootUnitId) {
      await apiFetch(`/api/org-units/${rootUnitId}`, {
        method: "PUT",
        token,
        body: JSON.stringify({
          set_company_profile: true,
          company_profile: value,
        }),
      });
    }

    // Complete onboarding
    await apiFetch("/api/auth/onboarding/complete", {
      method: "POST",
      token,
    });

    router.push("/");
    router.refresh();
  }

  const stepIndex = step === "workspace" ? 0 : 1;

  return (
    <div className="w-full max-w-2xl">
      {/* Step indicator */}
      <nav aria-label="Onboarding progress" className="mb-10">
        <div className="flex items-center justify-center gap-3">
          {/* Step 1 */}
          <div className="flex items-center gap-2">
            <span
              className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-semibold transition-colors duration-200 ${
                stepIndex === 0
                  ? "bg-blue-600 text-white"
                  : "bg-blue-100 text-blue-700"
              }`}
            >
              {stepIndex > 0 ? (
                <svg
                  className="w-4 h-4"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth={2.5}
                  stroke="currentColor"
                  aria-hidden="true"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M4.5 12.75l6 6 9-13.5"
                  />
                </svg>
              ) : (
                "1"
              )}
            </span>
            <span
              className={`text-sm font-medium transition-colors duration-200 ${
                stepIndex === 0 ? "text-zinc-900" : "text-zinc-500"
              }`}
            >
              Workspace
            </span>
          </div>

          <div
            className={`w-12 h-px transition-colors duration-200 ${
              stepIndex > 0 ? "bg-blue-300" : "bg-zinc-200"
            }`}
          />

          {/* Step 2 */}
          <div className="flex items-center gap-2">
            <span
              className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-semibold transition-colors duration-200 ${
                stepIndex === 1
                  ? "bg-blue-600 text-white"
                  : "bg-zinc-100 text-zinc-400"
              }`}
            >
              2
            </span>
            <span
              className={`text-sm font-medium transition-colors duration-200 ${
                stepIndex === 1 ? "text-zinc-900" : "text-zinc-400"
              }`}
            >
              Company Profile
            </span>
          </div>
        </div>
      </nav>

      {/* Step 1: Workspace Type */}
      {step === "workspace" && (
        <div>
          <div className="text-center mb-8">
            <h1 className="text-2xl font-semibold text-zinc-900">
              How will you use ProjectX?
            </h1>
            <p className="text-sm text-zinc-500 mt-2 leading-relaxed max-w-md mx-auto">
              This helps us tailor your workspace to your hiring workflow.
              You can change this later in settings.
            </p>
          </div>

          {workspaceError && (
            <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3 mb-6 text-center">
              {workspaceError}
            </p>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            {/* Enterprise Card */}
            <button
              type="button"
              disabled={workspaceLoading}
              onClick={() => handleSelectWorkspace("enterprise")}
              className={`group relative bg-white border-2 rounded-xl p-8 text-left transition-all duration-200 cursor-pointer
                ${
                  selectedMode === "enterprise" && workspaceLoading
                    ? "border-blue-500 ring-2 ring-blue-200"
                    : "border-zinc-200 hover:border-blue-400 hover:ring-2 hover:ring-blue-100"
                }
                disabled:cursor-wait
              `}
              aria-label="Select enterprise workspace: hiring for your own company"
            >
              {selectedMode === "enterprise" && workspaceLoading && (
                <div className="absolute top-4 right-4">
                  <svg
                    className="w-5 h-5 text-blue-600 animate-spin"
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
                </div>
              )}

              <div className="w-12 h-12 rounded-lg bg-blue-50 flex items-center justify-center mb-5 group-hover:bg-blue-100 transition-colors duration-200">
                {/* Building icon */}
                <svg
                  className="w-6 h-6 text-blue-600"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth={1.5}
                  stroke="currentColor"
                  aria-hidden="true"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5m3-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75c.621 0 1.125.504 1.125 1.125V21"
                  />
                </svg>
              </div>

              <h2 className="text-base font-semibold text-zinc-900 mb-1.5">
                We&apos;re hiring for our own company
              </h2>
              <p className="text-sm text-zinc-500 leading-relaxed">
                Set up your workspace to manage internal hiring pipelines
              </p>
            </button>

            {/* Agency Card */}
            <button
              type="button"
              disabled={workspaceLoading}
              onClick={() => handleSelectWorkspace("agency")}
              className={`group relative bg-white border-2 rounded-xl p-8 text-left transition-all duration-200 cursor-pointer
                ${
                  selectedMode === "agency" && workspaceLoading
                    ? "border-blue-500 ring-2 ring-blue-200"
                    : "border-zinc-200 hover:border-blue-400 hover:ring-2 hover:ring-blue-100"
                }
                disabled:cursor-wait
              `}
              aria-label="Select agency workspace: recruiting for multiple clients"
            >
              {selectedMode === "agency" && workspaceLoading && (
                <div className="absolute top-4 right-4">
                  <svg
                    className="w-5 h-5 text-blue-600 animate-spin"
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
                </div>
              )}

              <div className="w-12 h-12 rounded-lg bg-violet-50 flex items-center justify-center mb-5 group-hover:bg-violet-100 transition-colors duration-200">
                {/* Users/people icon */}
                <svg
                  className="w-6 h-6 text-violet-600"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth={1.5}
                  stroke="currentColor"
                  aria-hidden="true"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M18 18.72a9.094 9.094 0 003.741-.479 3 3 0 00-4.682-2.72m.94 3.198l.001.031c0 .225-.012.447-.037.666A11.944 11.944 0 0112 21c-2.17 0-4.207-.576-5.963-1.584A6.062 6.062 0 016 18.719m12 0a5.971 5.971 0 00-.941-3.197m0 0A5.995 5.995 0 0012 12.75a5.995 5.995 0 00-5.058 2.772m0 0a3 3 0 00-4.681 2.72 8.986 8.986 0 003.74.477m.94-3.197a5.971 5.971 0 00-.94 3.197M15 6.75a3 3 0 11-6 0 3 3 0 016 0zm6 3a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0zm-13.5 0a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0z"
                  />
                </svg>
              </div>

              <h2 className="text-base font-semibold text-zinc-900 mb-1.5">
                We&apos;re a recruiting agency
              </h2>
              <p className="text-sm text-zinc-500 leading-relaxed">
                Set up your workspace to manage hiring for multiple clients
              </p>
            </button>
          </div>
        </div>
      )}

      {/* Step 2: Company Profile */}
      {step === "company-profile" && (
        <div>
          <div className="text-center mb-8">
            <h1 className="text-2xl font-semibold text-zinc-900">
              Tell us about your company
            </h1>
            <p className="text-sm text-zinc-500 mt-2 leading-relaxed max-w-md mx-auto">
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
            <div className="bg-white border border-zinc-200 rounded-xl p-7">
              {profileError && (
                <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3 mb-6">
                  {profileError}
                </p>
              )}
              <CompanyProfileForm
                onSubmit={async (value: CompanyProfile) => {
                  try {
                    await handleSubmitProfile(value);
                  } catch (err) {
                    setProfileError(
                      err instanceof Error
                        ? err.message
                        : "Failed to save company profile"
                    );
                    throw err;
                  }
                }}
                submitLabel="Finish Onboarding"
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
