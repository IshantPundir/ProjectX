"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";
import { authApi } from "@/lib/api/auth";
import { orgUnitsApi } from "@/lib/api/org-units";
import { applyApiErrorToForm } from "@/lib/api/errors";
import { Button, Input, Textarea, Label } from "@/components/px";

const onboardingProfileSchema = z.object({
  about: z.string().min(1, "Tell us what you build"),
  industry: z.string().min(1, "What industry?"),
  hiring_bar: z.string().min(1, "Describe a strong hire"),
});
type OnboardingProfileValues = z.infer<typeof onboardingProfileSchema>;

export default function OnboardingPage() {
  const router = useRouter();

  const [rootUnitId, setRootUnitId] = useState("");
  const [profileError, setProfileError] = useState("");
  const [fetchingOrg, setFetchingOrg] = useState(true);

  const form = useForm<OnboardingProfileValues>({
    resolver: zodResolver(onboardingProfileSchema),
    defaultValues: {
      about: "",
      industry: "",
      hiring_bar: "",
    },
    mode: "onChange",
  });

  const aboutValue = form.watch("about") || "";
  const hiringBarValue = form.watch("hiring_bar") || "";

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

  async function handleSubmitProfile(values: OnboardingProfileValues) {
    setProfileError("");

    const token = await getToken();
    if (!token) return;

    if (rootUnitId) {
      await orgUnitsApi.update(token, rootUnitId, {
        about: values.about, set_about: true,
        industry: values.industry, set_industry: true,
        hiring_bar: values.hiring_bar, set_hiring_bar: true,
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
          Three questions about your company. This takes about 2 minutes and
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
          <form
            onSubmit={form.handleSubmit(async (values) => {
              try {
                await handleSubmitProfile(values);
              } catch (err) {
                if (
                  applyApiErrorToForm(err, form, {
                    stripPrefixes: ['body', 'metadata'],
                  })
                ) {
                  return;
                }
                setProfileError(
                  err instanceof Error
                    ? err.message
                    : 'Failed to save company profile',
                );
              }
            })}
            className="space-y-6"
          >
            <div>
              <div className="flex items-baseline justify-between">
                <Label htmlFor="about" className="text-sm font-semibold">
                  What does your company actually build or do?
                </Label>
                <span className="text-xs text-zinc-400">{aboutValue.length} / 500</span>
              </div>
              <p className="text-xs text-zinc-500 mt-1 mb-2">
                Be specific — what problems, at what scale, for whom?{' '}
                <em>Not your mission statement.</em>
              </p>
              <Textarea id="about" {...form.register("about")} rows={4} />
              {form.formState.errors.about && (
                <p className="text-xs text-red-500 mt-1">
                  {form.formState.errors.about.message}
                </p>
              )}
            </div>

            <div>
              <Label htmlFor="industry" className="text-sm font-semibold">
                Industry
              </Label>
              <p className="text-xs text-zinc-500 mt-1 mb-2">
                e.g. SaaS / Enterprise Software, Fintech, Healthcare, E-commerce…
              </p>
              <Input
                id="industry"
                type="text"
                placeholder="Your industry"
                {...form.register("industry")}
              />
              {form.formState.errors.industry && (
                <p className="text-xs text-red-500 mt-1">
                  {form.formState.errors.industry.message}
                </p>
              )}
            </div>

            <div>
              <div className="flex items-baseline justify-between">
                <Label htmlFor="hiring_bar" className="text-sm font-semibold">
                  What does a strong hire look like here?
                </Label>
                <span className="text-xs text-zinc-400">
                  {hiringBarValue.length} / 280
                </span>
              </div>
              <p className="text-xs text-zinc-500 mt-1 mb-2">
                What do you value that a generic JD wouldn&apos;t capture?
              </p>
              <Textarea id="hiring_bar" {...form.register("hiring_bar")} rows={3} />
              {form.formState.errors.hiring_bar && (
                <p className="text-xs text-red-500 mt-1">
                  {form.formState.errors.hiring_bar.message}
                </p>
              )}
            </div>

            <Button
              type="submit"
              disabled={!form.formState.isValid || form.formState.isSubmitting}
            >
              {form.formState.isSubmitting ? 'Saving...' : 'Finish Onboarding'}
            </Button>
          </form>
        </div>
      )}
    </div>
  );
}
