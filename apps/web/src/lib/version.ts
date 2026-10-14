export async function getDeploymentVersion(
  webSha: string,
  apiUrl: string,
  fetcher: typeof fetch = fetch,
  correlationId?: string,
): Promise<{ git_sha: string; api_git_sha: string } | null> {
  try {
    const response = await fetcher(`${apiUrl}/api/v1/version`, {
      cache: "no-store",
      ...(correlationId ? { headers: { "X-Request-ID": correlationId } } : {}),
      signal: AbortSignal.timeout(3000),
    });
    if (!response.ok) return null;
    const data: unknown = await response.json();
    if (
      !data ||
      typeof data !== "object" ||
      !("git_sha" in data) ||
      typeof data.git_sha !== "string" ||
      data.git_sha !== webSha
    )
      return null;
    return { git_sha: webSha, api_git_sha: data.git_sha };
  } catch {
    return null;
  }
}
