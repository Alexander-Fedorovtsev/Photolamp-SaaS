import { NextRequest, NextResponse } from "next/server";
import { proxyStaffRequest } from "@/lib/admin-proxy";

const resources = new Set(["branches", "models", "assets", "pricing"]);

export async function PATCH(
  request: NextRequest,
  context: { params: Promise<{ resource: string; id: string }> },
) {
  const { resource, id } = await context.params;
  const tenantId = request.nextUrl.searchParams.get("tenant_id");
  if (!resources.has(resource) || !tenantId) {
    return NextResponse.json({ detail: "Invalid catalog request" }, { status: 400 });
  }
  return proxyStaffRequest(
    `/api/v1/t/${encodeURIComponent(tenantId)}/${resource}/${encodeURIComponent(id)}`,
    { method: "PATCH", body: await request.text() },
  );
}
