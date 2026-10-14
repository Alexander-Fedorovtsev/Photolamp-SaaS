import { NextRequest, NextResponse } from "next/server";
import { apiInternalUrl } from "@/lib/api";
import { forwardedClientHeaders, storeStaffSession } from "@/lib/admin-proxy";

const tokenPattern = /^pli_[A-Za-z0-9_-]{32,128}$/;
const loginPattern = /^[a-z][a-z0-9._-]{3,31}$/;

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => null)) as {
    invitation_token?: unknown;
    login_name?: unknown;
    password?: unknown;
  } | null;
  const token = typeof body?.invitation_token === "string" ? body.invitation_token : "";
  const loginName =
    typeof body?.login_name === "string" ? body.login_name.trim().toLowerCase() : "";
  if (
    !tokenPattern.test(token) ||
    !loginPattern.test(loginName) ||
    typeof body?.password !== "string" ||
    body.password.length < 12 ||
    body.password.length > 128
  ) {
    return NextResponse.json({ detail: "Проверьте данные приглашения" }, { status: 400 });
  }
  const response = await fetch(`${apiInternalUrl()}/api/v1/auth/staff/invitations/redeem`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...forwardedClientHeaders(request) },
    body: JSON.stringify({
      invitation_token: token,
      login_name: loginName,
      password: body.password,
    }),
    cache: "no-store",
  });
  const payload = (await response.json().catch(() => ({}))) as {
    access_token?: string;
    expires_at?: string;
  };
  if (!response.ok || !payload.access_token || !payload.expires_at) {
    return NextResponse.json(
      { detail: "Ссылка недействительна, истекла или логин уже занят" },
      { status: response.status },
    );
  }
  await storeStaffSession(request, payload.access_token, payload.expires_at);
  return NextResponse.json({ authenticated: true }, { headers: { "Cache-Control": "no-store" } });
}
