import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Grace — caseworker queue",
  description: "Renewals Grace filed, and the cases it refused to decide.",
};

/** Two destinations, because there are two questions: what happened in the last
 *  sweep, and what is waiting on me. Anything else would be navigation for its
 *  own sake. */
const NAV = [
  { href: "/", label: "Sweep" },
  { href: "/queue", label: "Queue" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-paper text-ink antialiased">
        <header className="border-b border-rule">
          <div className="mx-auto flex max-w-5xl flex-wrap items-baseline gap-x-8 gap-y-2 px-6 py-4">
            <p className="flex items-baseline gap-2">
              <span className="text-base font-semibold tracking-tight">Grace</span>
              <span className="font-mono text-[0.6875rem] uppercase tracking-[0.12em] text-muted">
                caseworker queue
              </span>
            </p>
            <nav aria-label="Sections" className="flex gap-5 font-mono text-xs">
              {NAV.map(item => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="text-muted underline decoration-transparent decoration-2 underline-offset-4 transition-colors hover:text-ink hover:decoration-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-6 py-10">{children}</main>
      </body>
    </html>
  );
}
