import { NextRequest, NextResponse } from "next/server";
import { proxyStaffRequest } from "@/lib/admin-proxy";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const decisions = {
  extension: new Set(["approve", "reject"]),
  pickup: new Set(["acknowledge", "reject"]),
} as const;

export async function POST(
  request: NextRequest,
  context: {
    params: Promise<{ orderId: string; kind: string; requestId: string; decision: string }>;
  },
) {
  const tenantId = request.nextUrl.searchParams.get("tenant_id");
  const idempotencyKey = request.headers.get("Idempotency-Key");
  const { orderId, kind, requestId, decision } = await context.params;
  if (
    !tenantId ||
    !uuidPattern.test(tenantId) ||
    !uuidPattern.test(orderId) ||
    !uuidPattern.test(requestId) ||
    (kind !== "extension" && kind !== "pickup") ||
    !decisions[kind].has(decision) ||
    !idempotencyKey ||
    idempotencyKey.length < 16 ||
    idempotencyKey.length > 128
  ) {
    return NextResponse.json({ detail: "Invalid request decision" }, { status: 400 });
  }
  const backendKind = kind === "extension" ? "extension-requests" : "pickup-requests";
  const body = decision === "reject" ? await request.text() : undefined;
  if (body && body.length > 500) {
    return NextResponse.json({ detail: "Invalid request decision" }, { status: 400 });
  }
  return proxyStaffRequest(
    `/api/v1/t/${tenantId}/orders/${orderId}/${backendKind}/${requestId}/${decision}`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body,
    },
  );
}
