import { apiInternalUrl } from "@/lib/api";

export async function GET() {
  let ready = false;
  try {
    const response = await fetch(`${apiInternalUrl()}/api/v1/health/ready`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    ready = response.ok && (await response.json()).status === "ready";
  } catch {
    ready = false;
  }
  return Response.json(
    { status: ready ? "ready" : "not_ready" },
    {
      status: ready ? 200 : 503,
      headers: { "Cache-Control": "no-store" },
    },
  );
}
