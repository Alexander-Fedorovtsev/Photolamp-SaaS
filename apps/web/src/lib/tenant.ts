import { z } from "zod";

export const tenantSchema = z.object({
  slug: z
    .string()
    .regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/)
    .max(63),
  display_name: z.string().min(1).max(100),
  city: z.string().min(1).max(100),
  timezone: z.string().min(1),
  accent: z.enum(["sun", "mint"]),
  is_demo: z.literal(true),
  booking_enabled: z.literal(false),
  phone: z.null(),
  tariff: z.null(),
});

export type TenantPreview = z.infer<typeof tenantSchema>;

export class PreviewUnavailable extends Error {
  constructor() {
    super("Tenant preview service is unavailable");
  }
}

export async function getTenantPreview(
  slug: string,
  apiUrl: string,
  fetcher: typeof fetch = fetch,
): Promise<TenantPreview | null> {
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug) || slug.length > 63) return null;
  try {
    const response = await fetcher(`${apiUrl}/api/v1/r/${encodeURIComponent(slug)}/preview`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (response.status === 404) return null;
    if (!response.ok) throw new PreviewUnavailable();
    const result = tenantSchema.parse(await response.json());
    if (result.slug !== slug) throw new PreviewUnavailable();
    return result;
  } catch {
    throw new PreviewUnavailable();
  }
}
