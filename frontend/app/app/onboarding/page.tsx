"use client";

import { useRouter } from "next/navigation";

export default function OnboardingPage() {
  const router = useRouter();

  return (
    <div className="text-center max-w-md">
      <div className="w-12 h-12 rounded-full bg-green-50 flex items-center justify-center mx-auto mb-4">
        <span className="text-green-600 text-xl">&#x2713;</span>
      </div>
      <h1 className="text-xl font-semibold text-zinc-900 mb-2">
        Welcome to ProjectX
      </h1>
      <p className="text-sm text-zinc-500 leading-relaxed mb-6">
        Your account has been created successfully. The onboarding wizard is
        coming soon — for now, you can explore the dashboard.
      </p>
      <button
        onClick={() => {
          router.push("/");
          router.refresh();
        }}
        className="bg-green-600 text-white px-6 py-2.5 rounded-lg text-sm font-medium hover:bg-green-700"
      >
        Go to Dashboard
      </button>
    </div>
  );
}
