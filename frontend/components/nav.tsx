"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/opportunities", label: "Opportunities" },
  { href: "/simulation", label: "Simulation" },
  { href: "/analytics", label: "Analytics" },
  { href: "/razorpay", label: "Razorpay" },
  { href: "/demo", label: "Demo" },
  { href: "/settings", label: "Settings" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="sticky top-0 z-20 border-b border-line bg-surface/95 backdrop-blur">
      <div className="mx-auto flex max-w-[1400px] items-center gap-6 px-5 py-2.5">
        <Link href="/" className="flex shrink-0 items-center gap-2">
          <span
            aria-hidden
            className="grid h-6 w-6 place-items-center rounded-md text-[11px] font-bold text-white"
            style={{ background: "var(--series-1)" }}
          >
            R
          </span>
          <span className="text-sm font-semibold tracking-tight text-ink">RecoverAI</span>
        </Link>

        <nav className="flex flex-1 items-center gap-0.5 overflow-x-auto">
          {LINKS.map((link) => {
            const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={`whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs font-medium transition ${
                  active ? "bg-surface-2 text-ink" : "text-ink-2 hover:text-ink"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        <div className="flex shrink-0 items-center gap-3">
          <span className="hidden items-center gap-1.5 text-2xs text-ink-muted sm:flex">
            <span aria-hidden className="live-dot h-1.5 w-1.5 rounded-full bg-good" />
            Test Mode
          </span>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark" | null>(null);

  useEffect(() => {
    const stored = window.localStorage.getItem("recoverai-theme");
    if (stored === "light" || stored === "dark") {
      setTheme(stored);
      document.documentElement.dataset.theme = stored;
    }
  }, []);

  function toggle() {
    const current =
      theme ??
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try {
      window.localStorage.setItem("recoverai-theme", next);
    } catch {
      /* private browsing: the toggle still works for this session */
    }
  }

  return (
    <button
      onClick={toggle}
      className="rounded-md border border-line-strong px-2 py-1 text-2xs text-ink-2 transition hover:text-ink"
      aria-label="Toggle colour theme"
    >
      {theme === "dark" ? "Light" : "Dark"}
    </button>
  );
}
