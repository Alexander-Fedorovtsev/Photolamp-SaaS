import { NextRequest, NextResponse } from "next/server";
import { proxyStaffRequest } from "@/lib/admin-proxy";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const actions = new Set(["accept", "pickup", "deliver", "contact"]);

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ taskId: string; action: string }> },
) {
  const tenantId = request.nextUrl.searchParams.get("tenant_id");
  const key = request.headers.get("Idempotency-Key");
  const { taskId, action } = await context.params;
  if (
    !tenantId ||
    !uuidPattern.test(tenantId) ||
    !uuidPattern.test(taskId) ||
    !actions.has(action) ||
    !key ||
    key.length < 16 ||
    key.length > 128
  ) {
    return NextResponse.json({ detail: "Invalid courier task command" }, { status: 400 });
  }
  const backendAction = action === "contact" ? "contact-access" : action;
  return proxyStaffRequest(`/api/v1/t/${tenantId}/courier/tasks/${taskId}/${backendAction}`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
  });
}
