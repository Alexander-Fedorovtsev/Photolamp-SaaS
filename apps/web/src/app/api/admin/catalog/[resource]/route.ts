import { NextRequest, NextResponse } from "next/server";
import { proxyStaffRequest } from "@/lib/admin-proxy";

const resources = new Set(["branches", "models", "assets", "pricing"]);

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ resource: string }> },
) {
  const { resource } = await context.params;
  const tenantId = request.nextUrl.searchParams.get("tenant_id");
  if (!resources.has(resource) || !tenantId) {
    return NextResponse.json({ detail: "Invalid catalog request" }, { status: 400 });
  }
  return proxyStaffRequest(`/api/v1/t/${encodeURIComponent(tenantId)}/${resource}`, {
    method: "POST",
    body: await request.text(),
  });
}
