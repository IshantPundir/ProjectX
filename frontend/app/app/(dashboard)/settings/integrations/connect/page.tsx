"use client";

import { useState } from "react";

import { CeipalConnectionForm } from "@/components/settings/integrations/CeipalConnectionForm";

export default function ConnectPage() {
  // Future: vendor picker. For MVP only Ceipal is supported. The state
  // hook is kept so Greenhouse/Workday land as a one-line addition.
  const [vendor] = useState<"ceipal">("ceipal");

  return (
    <div className="mx-auto max-w-xl space-y-6 px-8 pb-10 pt-5">
      <div>
        <h1
          className="px-serif m-0 text-[30px] font-normal"
          style={{ letterSpacing: "-0.6px", color: "var(--px-fg)" }}
        >
          Connect ATS
        </h1>
        <p className="mt-1 text-sm text-zinc-500">
          Connect your ATS account so ProjectX can import clients, jobs, and
          candidates. Credentials are encrypted at rest.
        </p>
      </div>

      {vendor === "ceipal" && <CeipalConnectionForm />}
    </div>
  );
}
