import { apiInternalUrl } from "@/lib/api";
import { getDeploymentVersion } from "@/lib/version";

export const dynamic = "force-dynamic";

export async function GET() {
  const version = await getDeploymentVersion(
    process.env.PHOTOLAMP_DEPLOY_SHA ?? "development",
    apiInternalUrl(),
  );
  return Response.json(version ?? { status: "not_ready" }, {
    status: version ? 200 : 503,
    headers: { "Cache-Control": "no-store" },
  });
}
