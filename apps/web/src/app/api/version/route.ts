import type { NextRequest } from "next/server";
import { apiInternalUrl } from "@/lib/api";
import { requestId, requestIdHeader } from "@/lib/request-id";
import { getDeploymentVersion } from "@/lib/version";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const correlationId = requestId(request.headers.get(requestIdHeader));
  const version = await getDeploymentVersion(
    process.env.PHOTOLAMP_DEPLOY_SHA ?? "development",
    apiInternalUrl(),
    fetch,
    correlationId,
  );
  return Response.json(version ?? { status: "not_ready" }, {
    status: version ? 200 : 503,
    headers: { "Cache-Control": "no-store", [requestIdHeader]: correlationId },
  });
}
