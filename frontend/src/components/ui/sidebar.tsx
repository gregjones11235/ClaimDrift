"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export function Sidebar() {
  const pathname = usePathname();

  const links = [
    { href: "/", label: "Dashboard" },
    { href: "/live", label: "Live stream" },
    { href: "/patterns", label: "Patterns" },
  ];

  return (
    <div className="w-[200px] flex-shrink-0 bg-white min-h-screen flex flex-col border-r-0">
      <div className="p-4 border-b border-black">
        <div className="font-mono text-[15px] font-medium text-black tracking-wide flex items-center gap-2">
          <div className="w-4 h-4 bg-black"></div>
          ClaimDrift
        </div>
      </div>
      
      <div className="flex-1 py-4 flex flex-col gap-1 px-4">
        {links.map((link) => {
          const isActive = pathname === link.href;
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`px-3 py-2 text-[13px] font-sans flex items-center justify-between border ${
                isActive
                  ? "bg-black text-white border-black"
                  : "bg-white text-black border-transparent hover:border-black"
              }`}
            >
              {link.label}
              {link.href === "/live" && (
                <LiveIndicator />
              )}
            </Link>
          );
        })}
      </div>
      
      <div className="p-4 border-t border-black">
        <a href="https://github.com" target="_blank" rel="noreferrer" className="text-[13px] text-black hover:underline transition-colors">
          GitHub
        </a>
      </div>
    </div>
  );
}

import { useSseStore } from "@/lib/store/sse";
import { useEffect, useState } from "react";

function LiveIndicator() {
  const isListening = useSseStore((state) => state.isListening);
  const [mounted, setMounted] = useState(false);
  
  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted || !isListening) return null;

  return (
    <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse border border-black" />
  );
}
