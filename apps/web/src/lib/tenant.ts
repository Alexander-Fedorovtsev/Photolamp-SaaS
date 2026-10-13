import { z } from "zod";

const postgresUuid = z
  .string()
  .regex(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);

const branchSchema = z.object({
  id: postgresUuid,
  name: z.string().min(1).max(100),
  city: z.string().min(1).max(100),
  timezone: z.string().min(1),
  pickup_enabled: z.boolean(),
  delivery_enabled: z.boolean(),
});

const catalogItemSchema = z
  .object({
    id: postgresUuid,
    name: z.string().min(1).max(120),
    category: z.literal("phototherapy_lamp"),
    manufacturer: z.string().min(1).max(120).nullable(),
    description: z.string().min(1).max(500),
    included_items: z.array(z.string().min(1)).min(1).max(12),
    illustration: z.enum(["floor", "compact", "mobile"]),
    amount_minor_per_day: z.number().int().positive(),
    deposit_minor: z.number().int().nonnegative(),
    currency: z.literal("RUB"),
    minimum_days: z.number().int().min(1),
    maximum_days: z.number().int().min(1),
    price_is_demo: z.boolean(),
    listed_units: z.number().int().nonnegative(),
    serviceable_units: z.number().int().nonnegative(),
  })
  .refine((item) => item.serviceable_units <= item.listed_units)
  .refine((item) => item.minimum_days <= item.maximum_days);

export const tenantCatalogSchema = z.object({
  slug: z
    .string()
    .regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/)
    .max(63),
  display_name: z.string().min(1).max(100),
  city: z.string().min(1).max(100),
  timezone: z.string().min(1),
  accent: z.enum(["sun", "mint"]),
  public_note: z.string().min(1).max(240),
  is_demo: z.boolean(),
  booking_enabled: z.boolean(),
  availability_confirmed: z.literal(false),
  phone: z.null(),
  branches: z.array(branchSchema),
  items: z.array(catalogItemSchema),
});

export type TenantCatalog = z.infer<typeof tenantCatalogSchema>;
export type CatalogItem = z.infer<typeof catalogItemSchema>;

export class CatalogUnavailable extends Error {
  constructor() {
    super("Tenant catalog service is unavailable");
  }
}

export async function getTenantCatalog(
  slug: string,
  apiUrl: string,
  fetcher: typeof fetch = fetch,
): Promise<TenantCatalog | null> {
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug) || slug.length > 63) return null;
  try {
    const response = await fetcher(`${apiUrl}/api/v1/r/${encodeURIComponent(slug)}/catalog`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (response.status === 404) return null;
    if (!response.ok) throw new CatalogUnavailable();
    const result = tenantCatalogSchema.parse(await response.json());
    if (result.slug !== slug) throw new CatalogUnavailable();
    if (result.booking_enabled || result.availability_confirmed) throw new CatalogUnavailable();
    return result;
  } catch {
    throw new CatalogUnavailable();
  }
}
