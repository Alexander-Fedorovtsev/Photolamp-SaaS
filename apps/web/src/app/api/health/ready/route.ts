import type { NextRequest } from "next/server";
import { apiInternalUrl } from "@/lib/api";
import { requestId, requestIdHeader } from "@/lib/request-id";

export async function GET(request: NextRequest) {
  let ready = false;
  const correlationId = requestId(request.headers.get(requestIdHeader));
  try {
    const response = await fetch(`${apiInternalUrl()}/api/v1/health/ready`, {
      cache: "no-store",
      headers: { [requestIdHeader]: correlationId },
      signal: AbortSignal.timeout(3000),
    });
    ready = response.ok && (await response.json()).status === "ready";
  } catch {
    ready = false;
  }
  return Response.json(
    { status: ready ? "ready" : "not_ready" },
    {
      status: ready ? 200 : 503,
      headers: { "Cache-Control": "no-store", [requestIdHeader]: correlationId },
    },
  );
}
