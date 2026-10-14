import { cookies } from "next/headers";
import { proxyStaffRequest, staffSessionCookie } from "@/lib/admin-proxy";

export async function GET() {
  return proxyStaffRequest("/api/v1/auth/staff/sessions");
}

export async function DELETE() {
  const response = await proxyStaffRequest("/api/v1/auth/staff/sessions", { method: "DELETE" });
  if (response.ok) (await cookies()).delete(staffSessionCookie);
  return response;
}
