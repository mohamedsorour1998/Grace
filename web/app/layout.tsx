import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Grace — caseworker queue",
  description: "Renewals Grace filed, and the cases it refused to decide.",
};

/** Named in the caseworker's vocabulary, not Grace's.
 *
 *  "Sweep" and "Queue" were the internal words for these pages and neither says
 *  what you get: a sweep is what the agent does, and a queue could be anything.
 *  The page headline already reads "9 handled alone, 3 waiting on you", so the
 *  nav uses the same words the product already speaks.
 *
 *  "Add case" is first because it is the one thing here a caseworker *starts*
 *  rather than reviews. And `/new` is in this list at all because a page nothing
 *  links to is a page nobody can use — the intake form shipped reachable only by
 *  typing the URL, which is indistinguishable from not having shipped it. */
const NAV = [
  { href: "/new", label: "Add case" },
  { href: "/", label: "All households" },
  { href: "/queue", label: "Waiting on you" },
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
            <a
              href="/api/auth/logout"
              className="ml-auto font-mono text-xs text-muted underline decoration-transparent decoration-2 underline-offset-4 transition-colors hover:text-ink hover:decoration-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
            >
              Sign out
            </a>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-6 py-10">{children}</main>
      </body>
    </html>
  );
}
