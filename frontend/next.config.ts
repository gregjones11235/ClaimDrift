import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Next.js dev mode normally renders a floating build-status indicator in the
  // bottom-left corner. On this layout it overlaps the GitHub link in the
  // sidebar footer, so we hide it. Production builds never show it regardless.
  devIndicators: false,
};

export default nextConfig;
