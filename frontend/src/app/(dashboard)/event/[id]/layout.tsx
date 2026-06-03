"use client";

import { use } from "react";

export default function EventLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}) {
  return (
    <div className="space-y-4">
      {children}
    </div>
  );
}
