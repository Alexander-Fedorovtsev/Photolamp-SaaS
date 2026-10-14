import { NextRequest, NextResponse } from "next/server";
import { proxyStaffRequest } from "@/lib/admin-proxy";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function DELETE(
  request: NextRequest,
  context: { params: Promise<{ invitationId: string }> },
) {
  const tenantId = request.nextUrl.searchParams.get("tenant_id");
  const { invitationId } = await context.params;
  if (!tenantId || !uuidPattern.test(tenantId) || !uuidPattern.test(invitationId)) {
    return NextResponse.json({ detail: "Invalid invitation request" }, { status: 400 });
  }
  return proxyStaffRequest(`/api/v1/t/${tenantId}/team/invitations/${invitationId}`, {
    method: "DELETE",
  });
}
