// Server-side only: never expose the internal service address to the browser.
export function apiInternalUrl(): string {
  return process.env.API_INTERNAL_URL ?? "http://localhost:8000";
}
