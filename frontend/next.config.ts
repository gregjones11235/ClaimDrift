import type { NextConfig } from "next";

// The browser talks only to the frontend's own origin: /api/* is proxied to the BFF and /api/playground/* to the
// playground backend, so the session cookie (set on this origin, HttpOnly) reaches both. Cross-origin calls to the
// *.run.app backends would make it a third-party cookie, which browsers block.
const BFF_URL = process.env.BFF_INTERNAL_URL ?? process.env.NEXT_PUBLIC_BFF_URL ?? "http://127.0.0.1:8787";
const PLAYGROUND_URL = process.env.PLAYGROUND_INTERNAL_URL ?? process.env.NEXT_PUBLIC_PLAYGROUND_URL ?? "http://127.0.0.1:8799";

const nextConfig: NextConfig = {
  // Next.js dev mode normally renders a floating build-status indicator in the
  // bottom-left corner. On this layout it overlaps the GitHub link in the
  // sidebar footer, so we hide it. Production builds never show it regardless.
  devIndicators: false,
  async rewrites() {
    return [
      { source: "/api/playground/:path*", destination: `${PLAYGROUND_URL}/api/playground/:path*` },
      { source: "/api/:path*", destination: `${BFF_URL}/api/:path*` },
    ];
  },
  // The review queue moved from the customer area to the operator area (/ops) on 2026-10-03.
  async redirects() {
    return [
      { source: "/review", destination: "/ops/review", permanent: false },
      { source: "/review/:path*", destination: "/ops/review/:path*", permanent: false },
    ];
  },
};

export default nextConfig;
