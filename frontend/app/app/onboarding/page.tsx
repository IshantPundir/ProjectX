"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api/client";

const UNIT_TYPES = [
  {
    value: "department",
    label: "Department",
    description: "A functional department like Engineering or Sales",
  },
  {
    value: "team",
    label: "Team",
    description: "A specific team within your organization",
  },
  {
    value: "branch",
    label: "Branch",
    description: "A physical office or branch location",
  },
  {
    value: "region",
    label: "Region",
    description: "A geographic region grouping",
  },
  {
    value: "client_account",
    label: "Client Account",
    description: "An account for a specific client engagement",
  },
];

type Step = "create-unit" | "complete";

export default function OnboardingPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("create-unit");

  // Unit creation state
  const [unitName, setUnitName] = useState("");
  const [unitType, setUnitType] = useState("department");
  const [unitError, setUnitError] = useState("");
  const [unitLoading, setUnitLoading] = useState(false);

  // Completion state
  const [completeLoading, setCompleteLoading] = useState(false);
  const [completeError, setCompleteError] = useState("");

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

  async function handleCreateUnit(e: React.FormEvent) {
    e.preventDefault();
    setUnitError("");
    setUnitLoading(true);

    try {
      const token = await getToken();
      if (!token) return;

      await apiFetch("/api/org-units", {
        method: "POST",
        token,
        body: JSON.stringify({ name: unitName, unit_type: unitType }),
      });

      setStep("complete");
    } catch (err) {
      setUnitError(
        err instanceof Error ? err.message : "Failed to create organization"
      );
    } finally {
      setUnitLoading(false);
    }
  }

  async function handleComplete() {
    setCompleteError("");
    setCompleteLoading(true);

    try {
      const token = await getToken();
      if (!token) return;

      await apiFetch("/api/auth/onboarding/complete", {
        method: "POST",
        token,
      });

      router.push("/");
      router.refresh();
    } catch (err) {
      setCompleteError(
        err instanceof Error ? err.message : "Failed to complete onboarding"
      );
      setCompleteLoading(false);
    }
  }

  return (
    <div className="w-full max-w-lg">
      {/* Step indicator */}
      <div className="flex items-center justify-center gap-3 mb-8">
        <div className="flex items-center gap-2">
          <span
            className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold ${
              step === "create-unit"
                ? "bg-green-600 text-white"
                : "bg-green-100 text-green-700"
            }`}
          >
            {step === "complete" ? (
              <svg
                className="w-3.5 h-3.5"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth={3}
                stroke="currentColor"
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
            className={`text-xs font-medium ${
              step === "create-unit" ? "text-zinc-900" : "text-zinc-400"
            }`}
          >
            Create Unit
          </span>
        </div>
        <div className="w-8 h-px bg-zinc-200" />
        <div className="flex items-center gap-2">
          <span
            className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-semibold ${
              step === "complete"
                ? "bg-green-600 text-white"
                : "bg-zinc-100 text-zinc-400"
            }`}
          >
            2
          </span>
          <span
            className={`text-xs font-medium ${
              step === "complete" ? "text-zinc-900" : "text-zinc-400"
            }`}
          >
            Finish
          </span>
        </div>
      </div>

      {/* Step 1: Create org unit */}
      {step === "create-unit" && (
        <>
          <div className="text-center mb-6">
            <h1 className="text-xl font-semibold text-zinc-900">
              Welcome to ProjectX
            </h1>
            <p className="text-sm text-zinc-500 mt-2 leading-relaxed">
              Create your first organizational unit to structure your hiring
              teams. You can add more later.
            </p>
          </div>

          <form
            onSubmit={handleCreateUnit}
            className="bg-white border border-zinc-200 rounded-xl p-7 space-y-5"
          >
            {unitError && (
              <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">
                {unitError}
              </p>
            )}

            <div>
              <label
                htmlFor="unit-name"
                className="block text-xs font-medium text-zinc-600 mb-1.5"
              >
                Unit Name
              </label>
              <input
                id="unit-name"
                type="text"
                required
                value={unitName}
                onChange={(e) => setUnitName(e.target.value)}
                className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 focus:border-transparent"
                placeholder="e.g., Engineering, NYC Office"
              />
            </div>

            <div>
              <label
                htmlFor="unit-type"
                className="block text-xs font-medium text-zinc-600 mb-1.5"
              >
                Unit Type
              </label>
              <select
                id="unit-type"
                value={unitType}
                onChange={(e) => setUnitType(e.target.value)}
                className="w-full border border-zinc-300 rounded-lg px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-green-600 focus:border-transparent bg-white cursor-pointer"
              >
                {UNIT_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
              <p className="text-xs text-zinc-400 mt-1.5">
                {UNIT_TYPES.find((t) => t.value === unitType)?.description}
              </p>
            </div>

            <button
              type="submit"
              disabled={unitLoading || !unitName.trim()}
              className="w-full bg-green-600 text-white rounded-lg py-2.5 text-sm font-medium hover:bg-green-700 disabled:opacity-50 cursor-pointer transition-colors duration-150"
            >
              {unitLoading ? "Creating..." : "Create & Continue"}
            </button>
          </form>
        </>
      )}

      {/* Step 2: Complete onboarding */}
      {step === "complete" && (
        <div className="text-center">
          <div className="w-12 h-12 rounded-full bg-green-50 flex items-center justify-center mx-auto mb-4">
            <svg
              className="w-6 h-6 text-green-600"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth={2}
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M4.5 12.75l6 6 9-13.5"
              />
            </svg>
          </div>
          <h1 className="text-xl font-semibold text-zinc-900 mb-2">
            You're all set
          </h1>
          <p className="text-sm text-zinc-500 leading-relaxed mb-6 max-w-sm mx-auto">
            Your first organizational unit has been created. You can now invite
            team members and start configuring your interview pipelines.
          </p>

          {completeError && (
            <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3 mb-4">
              {completeError}
            </p>
          )}

          <button
            onClick={handleComplete}
            disabled={completeLoading}
            className="bg-green-600 text-white px-8 py-2.5 rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50 cursor-pointer transition-colors duration-150"
          >
            {completeLoading ? "Setting up..." : "Go to Dashboard"}
          </button>
        </div>
      )}
    </div>
  );
}
