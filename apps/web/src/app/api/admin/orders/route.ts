import { NextRequest, NextResponse } from "next/server";
import { proxyStaffRequest } from "@/lib/admin-proxy";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const states = new Set([
  "held",
  "confirmed",
  "in_progress",
  "returned",
  "completed",
  "expired",
  "cancelled",
]);

export async function GET(request: NextRequest) {
  const tenantId = request.nextUrl.searchParams.get("tenant_id");
  const state = request.nextUrl.searchParams.get("state");
  if (!tenantId || !uuidPattern.test(tenantId) || (state && !states.has(state))) {
    return NextResponse.json({ detail: "Invalid order queue request" }, { status: 400 });
  }
  const query = new URLSearchParams({ limit: "100", offset: "0" });
  if (state) query.set("state", state);
  return proxyStaffRequest(`/api/v1/t/${tenantId}/orders?${query}`);
}
