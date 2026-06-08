"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import Logo from "../landing/Logo";

/* ── nav items ── */
const MONITOR_LINKS = [
  {
    href: "/dashboard",
    label: "Dashboard",
    icon: (
      <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
        <rect x="1" y="1" width="5" height="5" rx=".5"/>
        <rect x="8" y="1" width="5" height="5" rx=".5"/>
        <rect x="1" y="8" width="5" height="5" rx=".5"/>
        <rect x="8" y="8" width="5" height="5" rx=".5"/>
      </svg>
    ),
  },
  {
    href: "/live",
    label: "Event stream",
    icon: (
      <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
        <circle cx="7" cy="7" r="2"/>
        <path d="M3.5 10.5A5 5 0 0 1 3.5 3.5M10.5 3.5A5 5 0 0 1 10.5 10.5"/>
      </svg>
    ),
  },
  {
    href: "/patterns",
    label: "Patterns",
    icon: (
      <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
        <path d="M7 1C4 1 1 4 1 7s3 6 6 6"/>
        <path d="M7 1c1.5 1.5 2.5 4 2.5 6S8.5 11.5 7 13"/>
        <path d="M1 7h12"/>
        <path d="M7 1c3 0 6 3 6 6s-3 6-6 6"/>
      </svg>
    ),
  },
];

// Playground is an expandable section (not a plain link): clicking the header
// toggles a sub-menu of experiments rather than navigating. See PlaygroundNav.
const PLAYGROUND_EXPERIMENTS: {
  href: string;
  label: string;
  comingSoon?: boolean;
}[] = [
  { href: "/playground/memory-ab", label: "A/B · Memory calibration" },
  { href: "/playground/orchestration", label: "5-Agent orchestration" },
];

const PLAYGROUND_ICON = (
  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
    <path d="M7 1v12"/>
    <rect x="1.5" y="3.5" width="3.5" height="7" rx=".5"/>
    <rect x="9" y="3.5" width="3.5" height="7" rx=".5"/>
  </svg>
);

const EVENT_LINKS = [
  {
    href: "/event",
    label: "Event detail",
    icon: (
      <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
        <rect x="1" y="3" width="12" height="10" rx=".8"/>
        <path d="M5 1v4M9 1v4M1 7h12"/>
      </svg>
    ),
    /* sub-pages shown when on /event/* */
    sub: [
      { href: "/event", label: "Overview" },
      { href: "/event/[id]/citations", label: "Citations" },
      { href: "/event/[id]/notifications", label: "Notifications" },
    ],
  },
  {
    href: "/citations",
    label: "Citations",
    icon: (
      <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
        <circle cx="5" cy="5" r="3"/>
        <path d="m10 10-2.5-2.5"/>
        <path d="M5 3v4M3 5h4"/>
      </svg>
    ),
    badgeColor: "rd",
  },
  {
    href: "/notifications",
    label: "Notifications",
    icon: (
      <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
        <path d="M7 1a4 4 0 0 1 4 4v3l1 2H2l1-2V5a4 4 0 0 1 4-4z"/>
        <path d="M5.5 11a1.5 1.5 0 0 0 3 0"/>
      </svg>
    ),
    badgeColor: "grn",
    badgeLabel: "sent",
  },
];

export function Sidebar() {
  const pathname = usePathname();

  const isActive = (href: string) =>
    href === "/dashboard" ? pathname === "/dashboard" : pathname?.startsWith(href);

  const isEventSection = pathname?.startsWith("/event") ||
    pathname?.startsWith("/citations") ||
    pathname?.startsWith("/notifications");

  const navItemStyle = (active: boolean): React.CSSProperties => ({
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "8px 16px",
    fontFamily: "var(--mono)",
    fontSize: 13,
    letterSpacing: "0.05em",
    textTransform: "uppercase",
    color: active ? "var(--y)" : "var(--gr)",
    background: active ? "rgba(245,197,24,0.07)" : "transparent",
    borderLeft: active ? "2px solid var(--y)" : "2px solid transparent",
    textDecoration: "none",
    transition: "all 0.15s",
    cursor: "pointer",
  });

  return (
    <aside style={{
      width: 260,
      flexShrink: 0,
      background: "var(--bk2)",
      borderRight: "1px solid var(--gr3)",
      display: "flex",
      flexDirection: "column",
      height: "100vh",
      position: "sticky",
      top: 0,
      zIndex: 50,
    }}>

      {/* Logo */}
      <div style={{ padding: "16px 18px", borderBottom: "1px solid var(--gr3)", display: "flex", alignItems: "center" }}>
        <Logo variant="sidebar" />
      </div>

      {/* Nav */}
      <nav style={{ padding: "12px 0", flex: 1, overflowY: "auto" }}>

        {/* Monitor section */}
        <div style={{ padding: "8px 16px 4px", fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.24em", textTransform: "uppercase", color: "var(--gr3)" }}>
          Monitor
        </div>
        {MONITOR_LINKS.map((link) => (
          <Link key={link.href} href={link.href} style={navItemStyle(isActive(link.href))}
            onMouseEnter={(e) => { if (!isActive(link.href)) { (e.currentTarget as HTMLElement).style.color = "var(--wh2)"; (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.03)"; } }}
            onMouseLeave={(e) => { if (!isActive(link.href)) { (e.currentTarget as HTMLElement).style.color = "var(--gr)"; (e.currentTarget as HTMLElement).style.background = "transparent"; } }}>
            <span style={{ width: 14, height: 14, display: "flex", alignItems: "center", justifyContent: "center", opacity: isActive(link.href) ? 1 : 0.7 }}>{link.icon}</span>
            <span style={{ flex: 1 }}>{link.label}</span>
          </Link>
        ))}

        {/* Playground — expandable section of experiments (not a plain link) */}
        <PlaygroundNav pathname={pathname} navItemStyle={navItemStyle} />

        {/* Events section */}
        <div style={{ padding: "12px 16px 4px", fontFamily: "var(--mono)", fontSize: 10, letterSpacing: "0.24em", textTransform: "uppercase", color: "var(--gr3)" }}>
          Events
        </div>

        {/* Dynamic Event Links */}
        {(() => {
          const eventIdMatch = pathname?.match(/^\/event\/([^/]+)/);
          const eventId = eventIdMatch ? eventIdMatch[1] : null;
          
          const eventHref = eventId ? `/event/${eventId}` : "/dashboard";
          const citationsHref = eventId ? `/event/${eventId}/citations` : "/dashboard";
          const notificationsHref = eventId ? `/event/${eventId}/notifications` : "/dashboard";

          const disabledStyle = !eventId ? { opacity: 0.4, cursor: "not-allowed" } : {};
          const disabledTitle = !eventId ? "Please select an event from the dashboard first" : undefined;

          return (
            <>
              {/* Event Detail with sub-nav */}
              <Link href={eventHref} 
                title={disabledTitle}
                onClick={(e) => { if (!eventId) e.preventDefault(); }}
                style={{ ...navItemStyle(isActive("/event") && !isActive("/citations") && !isActive("/notifications")), ...disabledStyle }}
                onMouseEnter={(e) => { if (eventId) (e.currentTarget as HTMLElement).style.color = "var(--wh2)"; }}
                onMouseLeave={(e) => { if (eventId) (e.currentTarget as HTMLElement).style.color = (isActive("/event") && !isActive("/citations") && !isActive("/notifications")) ? "var(--y)" : "var(--gr)"; }}>
                <span style={{ width: 14, height: 14, display: "flex", alignItems: "center", justifyContent: "center", opacity: 0.7 }}>
                  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
                    <rect x="1" y="3" width="12" height="10" rx=".8"/>
                    <path d="M5 1v4M9 1v4M1 7h12"/>
                  </svg>
                </span>
                Event detail
              </Link>



              {/* Citations direct link */}
              <Link href={citationsHref} 
                title={disabledTitle}
                onClick={(e) => { if (!eventId) e.preventDefault(); }}
                style={{ ...navItemStyle(isActive("/event/[id]/citations") || pathname?.endsWith("/citations") || false), ...disabledStyle }}
                onMouseEnter={(e) => { if (eventId && !isActive("/citations") && !pathname?.endsWith("/citations")) { (e.currentTarget as HTMLElement).style.color = "var(--wh2)"; (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.03)"; }}}
                onMouseLeave={(e) => { if (eventId && !isActive("/citations") && !pathname?.endsWith("/citations")) { (e.currentTarget as HTMLElement).style.color = "var(--gr)"; (e.currentTarget as HTMLElement).style.background = "transparent"; }}}>
                <span style={{ width: 14, height: 14, display: "flex", alignItems: "center", justifyContent: "center", opacity: (isActive("/citations") || pathname?.endsWith("/citations")) ? 1 : 0.7 }}>
                  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
                    <circle cx="5" cy="5" r="3"/><path d="m10 10-2.5-2.5"/><path d="M5 3v4M3 5h4"/>
                  </svg>
                </span>
                <span style={{ flex: 1 }}>Citations</span>
                <span style={{ fontFamily: "var(--mono)", fontSize: 10, padding: "1px 6px", border: "1px solid var(--rd)", color: "var(--rd)" }}>blast</span>
              </Link>

              {/* Notifications direct link */}
              <Link href={notificationsHref} 
                title={disabledTitle}
                onClick={(e) => { if (!eventId) e.preventDefault(); }}
                style={{ ...navItemStyle(isActive("/event/[id]/notifications") || pathname?.endsWith("/notifications") || false), ...disabledStyle }}
                onMouseEnter={(e) => { if (eventId && !isActive("/notifications") && !pathname?.endsWith("/notifications")) { (e.currentTarget as HTMLElement).style.color = "var(--wh2)"; (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.03)"; }}}
                onMouseLeave={(e) => { if (eventId && !isActive("/notifications") && !pathname?.endsWith("/notifications")) { (e.currentTarget as HTMLElement).style.color = "var(--gr)"; (e.currentTarget as HTMLElement).style.background = "transparent"; }}}>
                <span style={{ width: 14, height: 14, display: "flex", alignItems: "center", justifyContent: "center", opacity: (isActive("/notifications") || pathname?.endsWith("/notifications")) ? 1 : 0.7 }}>
                  <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
                    <path d="M7 1a4 4 0 0 1 4 4v3l1 2H2l1-2V5a4 4 0 0 1 4-4z"/>
                    <path d="M5.5 11a1.5 1.5 0 0 0 3 0"/>
                  </svg>
                </span>
                <span style={{ flex: 1 }}>Notifications</span>
                <span style={{ fontFamily: "var(--mono)", fontSize: 10, padding: "1px 6px", border: "1px solid var(--grn)", color: "var(--grn)" }}>log</span>
              </Link>
            </>
          );
        })()}

      </nav>

      {/* GitHub */}
      <a href="https://github.com/gregjones11235/ClaimDrift" target="_blank" rel="noopener"
        style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 16px", fontFamily: "var(--mono)", fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--gr2)", borderTop: "1px solid var(--gr3)", textDecoration: "none", transition: "color 0.15s" }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.color = "var(--wh)"; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.color = "var(--gr2)"; }}>
        <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
        </svg>
        GitHub
      </a>

      {/* Status footer */}
      <div style={{ padding: "12px 16px", borderTop: "1px solid var(--gr3)", display: "flex", alignItems: "center", gap: 7 }}>
        <div className="cd-status-dot" />
        <span className="specimen specimen-g">Monitoring active</span>
      </div>

    </aside>
  );
}

/* Playground = expandable section. The header toggles a sub-menu of experiments
   instead of navigating; it auto-expands when you're on any /playground route. */
function PlaygroundNav({
  pathname,
  navItemStyle,
}: {
  pathname: string | null;
  navItemStyle: (active: boolean) => React.CSSProperties;
}) {
  const onPlayground = pathname?.startsWith("/playground") ?? false;
  const [open, setOpen] = useState(onPlayground);

  return (
    <>
      {/* Section header — toggles open/closed, does NOT navigate */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen((v) => !v); } }}
        style={{ ...navItemStyle(onPlayground), userSelect: "none" }}
        onMouseEnter={(e) => { if (!onPlayground) { (e.currentTarget as HTMLElement).style.color = "var(--wh2)"; (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.03)"; } }}
        onMouseLeave={(e) => { if (!onPlayground) { (e.currentTarget as HTMLElement).style.color = "var(--gr)"; (e.currentTarget as HTMLElement).style.background = "transparent"; } }}
      >
        <span style={{ width: 14, height: 14, display: "flex", alignItems: "center", justifyContent: "center", opacity: onPlayground ? 1 : 0.7 }}>
          {PLAYGROUND_ICON}
        </span>
        <span style={{ flex: 1 }}>Playground</span>
        {/* chevron: points down when open, right when closed */}
        <svg
          width="10" height="10" viewBox="0 0 10 10" fill="none"
          stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
          style={{ transform: open ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 0.15s", opacity: 0.7 }}
        >
          <path d="M3 1.5 6.5 5 3 8.5" />
        </svg>
      </div>

      {/* Sub-items */}
      {open && PLAYGROUND_EXPERIMENTS.map((exp) => {
        const active = pathname?.startsWith(exp.href) ?? false;
        if (exp.comingSoon) {
          return (
            <div
              key={exp.href}
              title="Coming soon"
              style={{
                ...navItemStyle(false),
                paddingLeft: 40,
                fontSize: 12,
                opacity: 0.4,
                cursor: "not-allowed",
              }}
            >
              <span style={{ flex: 1 }}>{exp.label}</span>
              <span style={{ fontFamily: "var(--mono)", fontSize: 9, padding: "1px 6px", border: "1px solid var(--gr3)", color: "var(--gr)" }}>soon</span>
            </div>
          );
        }
        return (
          <Link
            key={exp.href}
            href={exp.href}
            style={{ ...navItemStyle(active), paddingLeft: 40, fontSize: 12 }}
            onMouseEnter={(e) => { if (!active) { (e.currentTarget as HTMLElement).style.color = "var(--wh2)"; (e.currentTarget as HTMLElement).style.background = "rgba(255,255,255,0.03)"; } }}
            onMouseLeave={(e) => { if (!active) { (e.currentTarget as HTMLElement).style.color = "var(--gr)"; (e.currentTarget as HTMLElement).style.background = "transparent"; } }}
          >
            <span style={{ flex: 1 }}>{exp.label}</span>
          </Link>
        );
      })}
    </>
  );
}
