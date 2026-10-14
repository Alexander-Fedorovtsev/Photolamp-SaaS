import { NextRequest, NextResponse } from "next/server";
import { proxyStaffRequest } from "@/lib/admin-proxy";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ orderId: string }> },
) {
  const tenantId = request.nextUrl.searchParams.get("tenant_id");
  const idempotencyKey = request.headers.get("Idempotency-Key");
  const { orderId } = await context.params;
  if (
    !tenantId ||
    !uuidPattern.test(tenantId) ||
    !uuidPattern.test(orderId) ||
    !idempotencyKey ||
    idempotencyKey.length < 16 ||
    idempotencyKey.length > 128
  ) {
    return NextResponse.json({ detail: "Invalid contact access request" }, { status: 400 });
  }
  return proxyStaffRequest(`/api/v1/t/${tenantId}/orders/${orderId}/contact-access`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
  });
}
