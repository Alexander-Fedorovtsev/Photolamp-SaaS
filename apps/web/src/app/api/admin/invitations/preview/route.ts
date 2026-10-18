import { NextRequest, NextResponse } from "next/server";
import { apiInternalUrl } from "@/lib/api";

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => null)) as { invitation_token?: unknown } | null;
  if (
    typeof body?.invitation_token !== "string" ||
    !/^pli_[A-Za-z0-9_-]{32,128}$/.test(body.invitation_token)
  ) {
    return NextResponse.json(
      { detail: "Ссылка приглашения недействительна." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  try {
    const response = await fetch(`${apiInternalUrl()}/api/v1/auth/staff/invitations/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    return NextResponse.json(
      response.ok
        ? await response.json()
        : {
            detail:
              "Ссылка уже использована, отозвана или истекла. Попросите владельца создать новое приглашение.",
          },
      {
        status: response.status,
        headers: { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" },
      },
    );
  } catch {
    return NextResponse.json(
      { detail: "Не удалось проверить приглашение. Обновите страницу или запросите новую ссылку." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}
