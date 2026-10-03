"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// Re-renders the (server-rendered) page every `seconds` while `active` -- used while a citation run is queued or
// running, so its progress line updates without a manual reload. Stops as soon as the page shows no active run.
export function AutoRefresh({ active, seconds = 10 }: { active: boolean; seconds?: number }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => router.refresh(), seconds * 1000);
    return () => clearInterval(t);
  }, [active, seconds, router]);
  return null;
}
