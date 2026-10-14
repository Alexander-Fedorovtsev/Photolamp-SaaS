import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { apiInternalUrl } from "@/lib/api";
import {
  forwardedClientHeaders,
  proxyStaffRequest,
  staffSessionCookie,
  storeStaffSession,
} from "@/lib/admin-proxy";
import { requestIdHeader } from "@/lib/request-id";

export async function GET() {
  return proxyStaffRequest("/api/v1/me/tenants");
}

export async function POST(request: NextRequest) {
  const forwardedHeaders = forwardedClientHeaders(request);
  const response = await fetch(`${apiInternalUrl()}/api/v1/auth/demo-session`, {
    method: "POST",
    headers: forwardedHeaders,
    cache: "no-store",
  });
  const payload = (await response.json()) as {
    access_token?: string;
    expires_at?: string;
    detail?: string;
  };
  if (!response.ok || !payload.access_token || !payload.expires_at) {
    return NextResponse.json(
      { detail: payload.detail ?? "Demo session unavailable" },
      {
        status: response.status,
        headers: {
          [requestIdHeader]:
            response.headers.get(requestIdHeader) ?? forwardedHeaders[requestIdHeader],
        },
      },
    );
  }
  await storeStaffSession(request, payload.access_token, payload.expires_at);
  return NextResponse.json(
    { authenticated: true, demo_only: true },
    {
      headers: {
        "Cache-Control": "no-store",
        [requestIdHeader]:
          response.headers.get(requestIdHeader) ?? forwardedHeaders[requestIdHeader],
      },
    },
  );
}

export async function DELETE() {
  const response = await proxyStaffRequest("/api/v1/auth/session", { method: "DELETE" });
  (await cookies()).delete(staffSessionCookie);
  return response;
}
