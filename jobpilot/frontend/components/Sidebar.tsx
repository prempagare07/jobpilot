"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  BriefcaseBusiness,
  ClipboardList,
  HelpCircle,
  Mail,
  Settings,
  Upload,
} from "lucide-react";

import { cn } from "@/lib/utils";

const navItems = [
  { href: "/", label: "Dashboard", icon: BarChart3 },
  { href: "/jobs", label: "Jobs", icon: BriefcaseBusiness },
  { href: "/applications", label: "Applications", icon: ClipboardList },
  { href: "/qa", label: "Q&A Memory", icon: HelpCircle },
  { href: "/outreach", label: "Outreach", icon: Mail },
  { href: "/resumes", label: "Resumes", icon: Upload },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="bg-slate-950 px-4 py-5 text-slate-100 lg:min-h-screen">
      <div className="flex items-center gap-3 px-2">
        <div className="flex h-9 w-9 items-center justify-center rounded-md bg-teal-500 text-slate-950">
          <BriefcaseBusiness className="h-5 w-5" aria-hidden="true" />
        </div>
        <div>
          <p className="text-sm font-semibold leading-none">JobPilot</p>
          <p className="mt-1 text-xs text-slate-400">Local command center</p>
        </div>
      </div>
      <nav className="mt-8 grid gap-1 sm:grid-cols-2 lg:grid-cols-1">
        {navItems.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex h-10 items-center gap-3 rounded-md px-3 text-sm font-medium text-slate-300 transition-colors hover:bg-slate-900 hover:text-white",
                active && "bg-slate-800 text-white",
              )}
            >
              <item.icon className="h-4 w-4" aria-hidden="true" />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
