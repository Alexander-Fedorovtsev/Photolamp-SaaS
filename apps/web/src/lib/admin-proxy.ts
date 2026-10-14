import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";
import { apiInternalUrl } from "@/lib/api";
import { requestId, requestIdHeader } from "@/lib/request-id";
import { secureSessionCookieRequired } from "@/lib/request-security";

export const staffSessionCookie = "photolamp_staff_session";
export const clientUserAgentHeader = "X-Photolamp-Client-User-Agent";

export function forwardedClientHeaders(request: NextRequest) {
  return {
    [clientUserAgentHeader]: (request.headers.get("user-agent") ?? "").slice(0, 512),
    [requestIdHeader]: requestId(request.headers.get(requestIdHeader)),
  };
}

export async function storeStaffSession(request: NextRequest, token: string, expiresAt: string) {
  const expires = new Date(expiresAt);
  if (!Number.isFinite(expires.getTime())) throw new Error("Invalid session expiry");
  (await cookies()).set(staffSessionCookie, token, {
    httpOnly: true,
    secure: secureSessionCookieRequired(request),
    sameSite: "strict",
    path: "/",
    expires,
  });
}

export async function proxyStaffRequest(path: string, init?: RequestInit) {
  const headers = new Headers(init?.headers);
  const correlationId = requestId(headers.get(requestIdHeader));
  const token = (await cookies()).get(staffSessionCookie)?.value;
  if (!token) {
    return NextResponse.json(
      { detail: "Staff session required" },
      { status: 401, headers: { [requestIdHeader]: correlationId } },
    );
  }
  headers.set(requestIdHeader, correlationId);
  headers.set("Authorization", `Bearer ${token}`);
  if (init?.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`${apiInternalUrl()}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
  const body = await response.text();
  return new NextResponse(body || null, {
    status: response.status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": response.headers.get("Content-Type") ?? "application/json",
      [requestIdHeader]: response.headers.get(requestIdHeader) ?? correlationId,
    },
  });
}
