import { NextRequest, NextResponse } from "next/server";
import { proxyStaffRequest } from "@/lib/admin-proxy";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET(request: NextRequest) {
  const tenantId = request.nextUrl.searchParams.get("tenant_id");
  if (!tenantId || !uuidPattern.test(tenantId)) {
    return NextResponse.json({ detail: "Invalid dispatch request" }, { status: 400 });
  }
  return proxyStaffRequest(`/api/v1/t/${tenantId}/courier/dispatch`);
}
