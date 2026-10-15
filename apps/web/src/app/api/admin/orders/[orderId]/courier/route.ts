import { NextRequest, NextResponse } from "next/server";
import { proxyStaffRequest } from "@/lib/admin-proxy";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function validated(request: NextRequest, orderId: string) {
  const tenantId = request.nextUrl.searchParams.get("tenant_id");
  const key = request.headers.get("Idempotency-Key");
  if (
    !tenantId ||
    !uuidPattern.test(tenantId) ||
    !uuidPattern.test(orderId) ||
    !key ||
    key.length < 16 ||
    key.length > 128
  ) {
    return null;
  }
  return { tenantId, key };
}

export async function PUT(request: NextRequest, context: { params: Promise<{ orderId: string }> }) {
  const { orderId } = await context.params;
  const values = await validated(request, orderId);
  if (!values) {
    return NextResponse.json({ detail: "Invalid courier assignment" }, { status: 400 });
  }
  const body = (await request.json().catch(() => null)) as { courier_user_id?: unknown } | null;
  if (
    !body ||
    typeof body.courier_user_id !== "string" ||
    !uuidPattern.test(body.courier_user_id)
  ) {
    return NextResponse.json({ detail: "Invalid courier assignment" }, { status: 400 });
  }
  return proxyStaffRequest(`/api/v1/t/${values.tenantId}/orders/${orderId}/courier-assignment`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "Idempotency-Key": values.key },
    body: JSON.stringify({ courier_user_id: body.courier_user_id }),
  });
}

export async function DELETE(
  request: NextRequest,
  context: { params: Promise<{ orderId: string }> },
) {
  const { orderId } = await context.params;
  const values = await validated(request, orderId);
  if (!values) {
    return NextResponse.json({ detail: "Invalid courier assignment" }, { status: 400 });
  }
  return proxyStaffRequest(`/api/v1/t/${values.tenantId}/orders/${orderId}/courier-assignment`, {
    method: "DELETE",
    headers: { "Idempotency-Key": values.key },
  });
}
