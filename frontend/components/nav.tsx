"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const ROUTES = [
  { href: "/", label: "Overview" },
  { href: "/requisitions", label: "Requisitions" },
  { href: "/campaigns", label: "Campaigns" },
  { href: "/sourcing", label: "Sourcing" },
  { href: "/attendance", label: "Attendance" },
];

export function Nav() {
  const pathname = usePathname();

  return (
    <nav className="border-b">
      <div className="mx-auto flex w-full max-w-5xl items-center gap-6 px-6">
        <span className="py-3 text-sm font-semibold">Hunar FDE</span>
        <div className="flex items-center gap-1">
          {ROUTES.map((route) => {
            const active =
              route.href === "/"
                ? pathname === "/"
                : pathname.startsWith(route.href);
            return (
              <Link
                key={route.href}
                href={route.href}
                className={cn(
                  "border-b-2 px-3 py-3 text-sm transition-colors",
                  active
                    ? "border-foreground font-medium text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                {route.label}
              </Link>
            );
          })}
        </div>
      </div>
    </nav>
  );
}
