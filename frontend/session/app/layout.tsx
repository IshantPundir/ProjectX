import type { Metadata } from "next";
import { Inter, Fraunces, JetBrains_Mono } from "next/font/google";
import type { ReactNode } from "react";

import { InterviewProviders } from "@/components/interview/providers";

import "./globals.css";

// CSP nonce in proxy.ts requires dynamic rendering — static-rendered
// pages have no request headers, so the nonce can't reach Next's
// emitted bootstrap scripts. Forcing dynamic on the layout applies to
// every route under it. The candidate flow is per-request anyway
// (token in URL, no caching desired).
export const dynamic = "force-dynamic";

const inter = Inter({
  variable: "--font-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

const fraunces = Fraunces({
  variable: "--font-serif",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  style: ["normal", "italic"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: "BinQle.ai Interview",
  description: "AI-led interview session",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${fraunces.variable} ${jetbrainsMono.variable} h-full antialiased`}
      data-px-theme="cool-light"
      data-px-density="comfortable"
    >
      {/*
       * suppressHydrationWarning silences the benign warning fired by
       * browser extensions (Grammarly, LanguageTool, password managers)
       * that inject attributes into <body> before React hydrates.
       * Suppression is scoped to a single element and only affects
       * attribute mismatches, not children — real hydration bugs in
       * the children below still surface normally.
       */}
      <body
        className="min-h-full flex flex-col bg-background text-foreground font-sans"
        suppressHydrationWarning
      >
        {/*
         * InterviewProviders (QueryClient + Toaster) is global and benign.
         * Interview-only chrome (AnimatedBackground, --px-app-base wrapper,
         * DevtoolsShield) lives in app/interview/layout.tsx so it does NOT
         * bleed into the public /recordings/* page.
         */}
        <InterviewProviders>
          {children}
        </InterviewProviders>
      </body>
    </html>
  );
}
