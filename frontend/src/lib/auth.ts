// Shared by server pages and client components.

// Only same-site paths are allowed as the post-login destination (no open redirect).
export function safeNext(next: string | undefined): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard";
}
