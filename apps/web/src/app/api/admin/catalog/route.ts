import { NextRequest, NextResponse } from "next/server";
import { proxyStaffRequest } from "@/lib/admin-proxy";

export async function GET(request: NextRequest) {
  const tenantId = request.nextUrl.searchParams.get("tenant_id");
  if (!tenantId) return NextResponse.json({ detail: "tenant_id required" }, { status: 400 });
  return proxyStaffRequest(`/api/v1/t/${encodeURIComponent(tenantId)}/catalog-admin`);
}
