import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RoadProof — Evidence-first Route Intelligence",
  description: "Multi-purpose, evidence-first road-type analysis for European routes.",
  other: {
    "codex-preview": "development",
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en"><body>{children}</body></html>
  );
}
