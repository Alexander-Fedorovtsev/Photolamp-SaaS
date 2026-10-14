import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { proxyStaffRequest, staffSessionCookie } from "@/lib/admin-proxy";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function DELETE(
  _request: NextRequest,
  context: { params: Promise<{ sessionId: string }> },
) {
  const { sessionId } = await context.params;
  if (!uuidPattern.test(sessionId)) {
    return NextResponse.json({ detail: "Invalid session request" }, { status: 400 });
  }
  const response = await proxyStaffRequest(`/api/v1/auth/staff/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (response.ok) {
    const payload = (await response
      .clone()
      .json()
      .catch(() => null)) as {
      revoked_current?: boolean;
    } | null;
    if (payload?.revoked_current) (await cookies()).delete(staffSessionCookie);
  }
  return response;
}
