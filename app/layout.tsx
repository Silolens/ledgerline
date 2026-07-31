import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Ledgerline — Deterministic Run",
  description:
    "Read-only view of one deterministic Ledgerline engine run: order outcomes, reconciliation drift, and a lineage-verified badge.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}