import type { Metadata } from "next";
import { Inter, Fraunces, JetBrains_Mono } from "next/font/google";
import type { ReactNode } from "react";

import { InterviewProviders } from "@/components/interview/providers";

import "./globals.css";

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
