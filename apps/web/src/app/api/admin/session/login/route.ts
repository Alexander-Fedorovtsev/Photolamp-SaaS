import { NextRequest, NextResponse } from "next/server";
import { apiInternalUrl } from "@/lib/api";
import { storeStaffSession } from "@/lib/admin-proxy";

const loginPattern = /^[a-z][a-z0-9._-]{3,31}$/;

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => null)) as {
    login_name?: unknown;
    password?: unknown;
  } | null;
  const loginName =
    typeof body?.login_name === "string" ? body.login_name.trim().toLowerCase() : "";
  if (
    !loginPattern.test(loginName) ||
    typeof body?.password !== "string" ||
    body.password.length < 12 ||
    body.password.length > 128
  ) {
    return NextResponse.json({ detail: "Проверьте логин и пароль" }, { status: 400 });
  }
  const response = await fetch(`${apiInternalUrl()}/api/v1/auth/staff/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login_name: loginName, password: body.password }),
    cache: "no-store",
  });
  const payload = (await response.json().catch(() => ({}))) as {
    access_token?: string;
    expires_at?: string;
  };
  if (!response.ok || !payload.access_token || !payload.expires_at) {
    return NextResponse.json({ detail: "Неверный логин или пароль" }, { status: response.status });
  }
  await storeStaffSession(request, payload.access_token, payload.expires_at);
  return NextResponse.json(
    { authenticated: true, demo_only: false },
    { headers: { "Cache-Control": "no-store" } },
  );
}
