import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Interim access gate: if SITE_PASSWORD is set, require HTTP Basic Auth (any username).
// Keeps the public production URL private until proper family magic-link auth lands.
// When SITE_PASSWORD is unset (e.g. local dev), the site is open.
export function middleware(req: NextRequest) {
  const expected = process.env.SITE_PASSWORD;
  if (!expected) return NextResponse.next();

  const header = req.headers.get("authorization");
  if (header?.startsWith("Basic ")) {
    const decoded = atob(header.slice(6));
    const password = decoded.slice(decoded.indexOf(":") + 1);
    if (password === expected) return NextResponse.next();
  }

  return new NextResponse("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="gold", charset="UTF-8"' },
  });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
