import { NextRequest, NextResponse } from "next/server";
import { proxyStaffRequest } from "@/lib/admin-proxy";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function POST(request: NextRequest) {
  const tenantId = request.nextUrl.searchParams.get("tenant_id");
  const idempotencyKey = request.headers.get("Idempotency-Key");
  const body = await request.text();
  if (
    !tenantId ||
    !uuidPattern.test(tenantId) ||
    !idempotencyKey ||
    idempotencyKey.length < 16 ||
    idempotencyKey.length > 128 ||
    body.length > 5_000
  ) {
    return NextResponse.json({ detail: "Invalid phone order request" }, { status: 400 });
  }
  return proxyStaffRequest(`/api/v1/t/${tenantId}/orders/phone`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body,
  });
}
