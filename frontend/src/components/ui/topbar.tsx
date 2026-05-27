"use client";

import { useSseStore } from "@/lib/store/sse";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

export function Topbar() {
  const pathname = usePathname();
  const isListening = useSseStore((state) => state.isListening);
  const [mounted, setMounted] = useState(false);
  
  useEffect(() => {
    setMounted(true);
  }, []);

  // Determine title from pathname
  let title = "Dashboard";
  if (pathname?.startsWith("/event/")) {
    if (pathname.includes("/citations")) title = "Citations";
    else if (pathname.includes("/notifications")) title = "Notification Log";
    else title = "Drift Detail";
  } else if (pathname === "/live") {
    title = "Agent Activity Stream";
  } else if (pathname === "/patterns") {
    title = "Pattern Memory";
  }

  return (
    <div className="h-[48px] border-b border-black px-8 flex items-center justify-between bg-white shrink-0">
      <div className="font-sans font-medium text-[15px] text-black">
        {title}
      </div>
      
      {mounted && (
        <div className="flex items-center gap-2">
          {isListening ? (
            <div className="flex items-center gap-1.5 px-2 py-1 border border-black bg-white text-[11px] font-medium text-black">
              <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse border border-black" />
              Live
            </div>
          ) : (
            <div className="flex items-center gap-1.5 px-2 py-1 border border-black bg-white text-[11px] font-medium text-black">
              <div className="w-1.5 h-1.5 rounded-full border border-black bg-white" />
              Disconnected
            </div>
          )}
        </div>
      )}
    </div>
  );
}
