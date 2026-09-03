import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Grace — caseworker queue",
  description: "Renewals Grace filed, and the cases it refused to decide.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <header className="border-b border-[var(--color-rule)] px-6 py-4">
          <span className="font-semibold">Grace</span>
          <span className="ml-2 text-sm text-[var(--color-muted)]">caseworker queue</span>
        </header>
        <main className="px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
