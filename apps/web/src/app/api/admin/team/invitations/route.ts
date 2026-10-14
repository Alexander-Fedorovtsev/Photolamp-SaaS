import { NextRequest, NextResponse } from "next/server";
import { proxyStaffRequest } from "@/lib/admin-proxy";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const roles = new Set(["owner", "admin", "operator", "courier"]);

function tenantId(request: NextRequest) {
  const value = request.nextUrl.searchParams.get("tenant_id");
  return value && uuidPattern.test(value) ? value : null;
}

export async function GET(request: NextRequest) {
  const selectedTenant = tenantId(request);
  if (!selectedTenant) {
    return NextResponse.json({ detail: "Invalid invitation request" }, { status: 400 });
  }
  return proxyStaffRequest(`/api/v1/t/${selectedTenant}/team/invitations`);
}

export async function POST(request: NextRequest) {
  const selectedTenant = tenantId(request);
  const body = (await request.json().catch(() => null)) as {
    display_name?: unknown;
    role?: unknown;
  } | null;
  const displayName = typeof body?.display_name === "string" ? body.display_name.trim() : "";
  if (
    !selectedTenant ||
    displayName.length < 1 ||
    displayName.length > 120 ||
    typeof body?.role !== "string" ||
    !roles.has(body.role)
  ) {
    return NextResponse.json({ detail: "Invalid invitation request" }, { status: 400 });
  }
  return proxyStaffRequest(`/api/v1/t/${selectedTenant}/team/invitations`, {
    method: "POST",
    body: JSON.stringify({ display_name: displayName, role: body.role }),
  });
}
