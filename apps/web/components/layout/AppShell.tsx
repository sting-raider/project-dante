"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import RailStatus from "@/components/commerce/RailStatus";

const navigation = [
  { href: "/", label: "Overview", icon: "⌂" },
  { href: "/buy", label: "Buyer desk", icon: "＋" },
  { href: "/merchant", label: "Merchant profile", icon: "▦" },
  { href: "/demo", label: "Demo room", icon: "▷" },
];

function activeFor(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function sectionLabel(pathname: string): string {
  if (pathname.startsWith("/buy")) return "Buyer desk";
  if (pathname.startsWith("/merchant")) return "Merchant profile";
  if (pathname.startsWith("/demo")) return "Demo room";
  if (pathname.startsWith("/contract")) return "Purchase dossier";
  if (pathname.startsWith("/audit")) return "Audit dossier";
  return "Overview";
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const section = sectionLabel(pathname);

  return (
    <div className="app-shell">
      <aside className="app-sidebar" aria-label="Project Dante navigation">
        <Link href="/" className="app-brand">
          <span className="app-brand-mark" aria-hidden="true">D</span>
          <span className="min-w-0">
            <span className="app-brand-name">Project Dante</span>
            <span className="app-brand-subtitle">Commerce runtime</span>
          </span>
        </Link>

        <div className="app-sidebar-section-label">Workspace</div>
        <nav className="app-nav" aria-label="Workspace">
          {navigation.map((item) => {
            const active = activeFor(pathname, item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`app-nav-link ${active ? "is-active" : ""}`}
              >
                <span className="app-nav-icon" aria-hidden="true">{item.icon}</span>
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="app-sidebar-spacer" />
        <div className="app-merchant-card">
          <span className="app-merchant-avatar" aria-hidden="true">A</span>
          <span className="min-w-0">
            <span className="app-merchant-name">Aster Electronics</span>
            <span className="app-merchant-caption">Merchant workspace</span>
          </span>
          <span className="app-online-dot" title="Runtime online" aria-label="Runtime online" />
        </div>
        <div className="app-rail-status">
          <RailStatus compact />
        </div>
      </aside>

      <div className="app-frame">
        <header className="app-topbar">
          <div className="app-mobile-brand">
            <span className="app-brand-mark" aria-hidden="true">D</span>
            <span>Project Dante</span>
          </div>
          <div className="app-breadcrumb" aria-label="Current location">
            <span className="app-breadcrumb-root">Workspace</span>
            <span aria-hidden="true">/</span>
            <span>{section}</span>
          </div>
          <div className="app-topbar-actions">
            <span className="app-topbar-note">Buyer-owned commerce</span>
            <Link href="/buy" className="app-topbar-cta">New brief <span aria-hidden="true">＋</span></Link>
          </div>
        </header>
        <div className="app-content">{children}</div>
      </div>
    </div>
  );
}
