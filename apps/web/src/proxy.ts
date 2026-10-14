import { NextRequest, NextResponse } from "next/server";
import { isSameOriginRequest } from "@/lib/request-security";

export function proxy(request: NextRequest) {
  if (!isSameOriginRequest(request)) {
    return NextResponse.json(
      { detail: "Same-origin request required" },
      { status: 403, headers: { "Cache-Control": "no-store" } },
    );
  }
  return NextResponse.next();
}

export const config = { matcher: "/api/admin/:path*" };
