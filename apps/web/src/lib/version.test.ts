import { expect, it, vi } from "vitest";
import { getDeploymentVersion } from "./version";

it("reports the revision only when both running images agree", async () => {
  const sha = "a".repeat(40);
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ git_sha: sha }));
  expect(await getDeploymentVersion(sha, "http://api:8000", fetcher)).toEqual({
    git_sha: sha,
    api_git_sha: sha,
  });
});

it.each([{ git_sha: "old-revision" }, { git_sha: 42 }, {}, null])(
  "rejects mismatched or invalid API revision",
  async (data) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(data));
    expect(await getDeploymentVersion("a".repeat(40), "http://api:8000", fetcher)).toBeNull();
  },
);

it("reports unavailable version on an API outage", async () => {
  const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error("private-detail"));
  expect(await getDeploymentVersion("development", "http://api:8000", fetcher)).toBeNull();
});
