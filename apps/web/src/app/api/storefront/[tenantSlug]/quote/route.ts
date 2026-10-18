import { NextRequest, NextResponse } from "next/server";
import { apiInternalUrl } from "@/lib/api";
import { quoteRequestSchema } from "@/lib/quote";

const slugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ tenantSlug: string }> },
) {
  const { tenantSlug } = await context.params;
  if (!slugPattern.test(tenantSlug) || tenantSlug.length > 63) {
    return NextResponse.json({ detail: "Storefront not found" }, { status: 404 });
  }
  const parsed = quoteRequestSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ detail: "Invalid quote request" }, { status: 422 });
  }
  const upstream = await fetch(
    `${apiInternalUrl()}/api/v1/r/${encodeURIComponent(tenantSlug)}/checkout/quote`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(parsed.data),
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    },
  );
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": upstream.headers.get("Content-Type") ?? "application/json",
    },
  });
}
