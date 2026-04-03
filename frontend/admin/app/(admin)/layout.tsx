"use client";

import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();

  async function handleSignOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <div className="flex flex-1">
      <aside className="w-56 border-r border-zinc-200 bg-white p-4 flex flex-col">
        <h2 className="text-sm font-bold text-zinc-900 mb-6">
          ProjectX Admin
        </h2>
        <nav className="flex-1">
          <a
            href="/dashboard"
            className="block text-sm text-zinc-700 hover:text-zinc-900 py-1.5"
          >
            Clients
          </a>
        </nav>
        <button
          onClick={handleSignOut}
          className="text-sm text-zinc-500 hover:text-zinc-700 text-left"
        >
          Sign out
        </button>
      </aside>
      <main className="flex-1 p-6">{children}</main>
    </div>
  );
}
