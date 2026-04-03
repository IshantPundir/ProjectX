"use client";

import { createClient } from "@/lib/supabase/client";
import { useRouter } from "next/navigation";

export default function PendingApprovalPage() {
  const router = useRouter();

  async function handleSignOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <div className="text-center max-w-sm">
        <h1 className="text-lg font-semibold text-zinc-900 mb-2">
          Pending Approval
        </h1>
        <p className="text-sm text-zinc-500 leading-relaxed">
          Your account has been created but is awaiting admin approval. You'll be
          able to access the dashboard once approved.
        </p>
        <button
          onClick={handleSignOut}
          className="text-sm text-blue-600 hover:underline mt-6"
        >
          Sign out
        </button>
      </div>
    </div>
  );
}
