import { NextResponse, type NextRequest } from "next/server";

// No anonymous access (decision 2026-10-03) except the landing page: a visitor without a session cookie is sent to
// /login, and back to the page they asked for after logging in ("Open dashboard" on the landing page -> /login). This is only the doorman: whether the session is valid and which role it has
// is checked by the BFF on every /api call and by the layouts (requireUser, admin check for /ops).
const SESSION_COOKIE = "cd_session";
const PUBLIC_PATHS = ["/", "/login", "/register"];

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  if (PUBLIC_PATHS.includes(pathname) || request.cookies.has(SESSION_COOKIE)) {
    return NextResponse.next();
  }
  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname + search)}`;
  return NextResponse.redirect(url);
}

export const config = {
  // /api/* is proxied to the backends, which answer 401 themselves; static assets stay public.
  matcher: ["/((?!api/|_next/static|_next/image|favicon.ico|.*\.(?:png|jpg|jpeg|svg|gif|webp|ico|txt)$).*)"],
};
