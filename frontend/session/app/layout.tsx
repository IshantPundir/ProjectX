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
  title: "ProjectX Interview",
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
      data-px-theme="warm-light"
      data-px-density="comfortable"
    >
      <body className="min-h-full flex flex-col bg-background text-foreground font-sans">
        <InterviewProviders>
          <div
            className="min-h-screen w-full"
            style={{
              background: "var(--px-bg)",
              color: "var(--px-fg)",
            }}
          >
            {children}
          </div>
        </InterviewProviders>
      </body>
    </html>
  );
}
