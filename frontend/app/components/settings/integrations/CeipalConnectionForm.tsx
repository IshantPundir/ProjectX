"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { Button, Input, Label } from "@/components/px";
import {
  ceipalCredentialsSchema,
  createConnection,
  type CeipalCredentials,
} from "@/lib/api/ats";
import { ApiError } from "@/lib/api/client";
import { getFreshSupabaseToken } from "@/lib/auth/tokens";

export function CeipalConnectionForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const {
    register,
    handleSubmit,
    formState: { errors },
    setError,
  } = useForm<CeipalCredentials>({
    resolver: zodResolver(ceipalCredentialsSchema),
    defaultValues: { email: "", password: "", api_key: "" },
  });

  const mutation = useMutation({
    mutationFn: async (values: CeipalCredentials) => {
      const token = await getFreshSupabaseToken();
      return createConnection(token, {
        vendor: "ceipal",
        credentials: values,
      });
    },
    onSuccess: (connection) => {
      toast.success("Ceipal connected. Initial sync started.");
      queryClient.invalidateQueries({ queryKey: ["ats", "connections"] });
      router.push(`/settings/integrations/${connection.id}`);
    },
    onError: (err) => {
      // The apiFetch client (lib/api/client.ts) lifts the server JSON
      // body's `code` field onto ApiError.code directly — surface the
      // backend's structured ATS_CREDENTIALS_INVALID code on the password
      // field rather than burying it in a generic toast.
      if (
        err instanceof ApiError &&
        err.status === 422 &&
        err.code === "ATS_CREDENTIALS_INVALID"
      ) {
        setError("password", {
          type: "server",
          message:
            "Ceipal rejected these credentials. Check email, password, and API key.",
        });
        return;
      }
      toast.error("Could not connect Ceipal. Please try again.");
    },
  });

  return (
    <form
      onSubmit={handleSubmit((values) => mutation.mutate(values))}
      className="space-y-4 rounded-[10px] border p-6"
      autoComplete="off"
      noValidate
      style={{
        background: "var(--px-surface)",
        borderColor: "var(--px-hairline)",
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor="ats-email">Ceipal account email</Label>
        <Input
          id="ats-email"
          type="email"
          autoComplete="off"
          {...register("email")}
        />
        {errors.email && (
          <p className="px-hint" style={{ color: "var(--px-danger)" }}>
            {errors.email.message}
          </p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="ats-password">Ceipal password</Label>
        <Input
          id="ats-password"
          type="password"
          autoComplete="new-password"
          {...register("password")}
        />
        {errors.password && (
          <p className="px-hint" style={{ color: "var(--px-danger)" }}>
            {errors.password.message}
          </p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="ats-api-key">API key</Label>
        <Input
          id="ats-api-key"
          type="password"
          autoComplete="off"
          {...register("api_key")}
        />
        {errors.api_key && (
          <p className="px-hint" style={{ color: "var(--px-danger)" }}>
            {errors.api_key.message}
          </p>
        )}
        <p className="px-hint">From Ceipal: Settings → Integrations → API.</p>
      </div>

      <div
        className="rounded-[10px] p-3 text-xs"
        style={{
          background: "color-mix(in oklab, var(--px-fg) 4%, transparent)",
          color: "var(--px-fg-3)",
        }}
      >
        These credentials are encrypted at rest with AES-128 (Fernet) and never
        appear in logs. ProjectX uses them only to fetch jobs, applicants, and
        submissions on a 15-minute interval.
      </div>

      <Button type="submit" disabled={mutation.isPending} className="w-full">
        {mutation.isPending ? "Testing connection…" : "Connect Ceipal"}
      </Button>
    </form>
  );
}
