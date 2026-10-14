import { NextRequest, NextResponse } from "next/server";
import { proxyStaffRequest } from "@/lib/admin-proxy";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function PATCH(
  request: NextRequest,
  context: { params: Promise<{ userId: string }> },
) {
  const tenantId = request.nextUrl.searchParams.get("tenant_id");
  const { userId } = await context.params;
  if (!tenantId || !uuidPattern.test(tenantId) || !uuidPattern.test(userId)) {
    return NextResponse.json({ detail: "Invalid team update request" }, { status: 400 });
  }
  const body = (await request.json().catch(() => null)) as {
    display_name?: unknown;
    role?: unknown;
    state?: unknown;
  } | null;
  const roles = new Set(["owner", "admin", "operator", "courier"]);
  const states = new Set(["active", "disabled"]);
  if (
    !body ||
    (body.display_name !== undefined &&
      (typeof body.display_name !== "string" ||
        body.display_name.trim().length < 1 ||
        body.display_name.trim().length > 120)) ||
    (body.role !== undefined && (typeof body.role !== "string" || !roles.has(body.role))) ||
    (body.state !== undefined && (typeof body.state !== "string" || !states.has(body.state))) ||
    (body.display_name === undefined && body.role === undefined && body.state === undefined)
  ) {
    return NextResponse.json({ detail: "Invalid team update request" }, { status: 400 });
  }
  return proxyStaffRequest(`/api/v1/t/${tenantId}/team/${userId}`, {
    method: "PATCH",
    body: JSON.stringify({
      ...body,
      ...(typeof body.display_name === "string" ? { display_name: body.display_name.trim() } : {}),
    }),
  });
}
