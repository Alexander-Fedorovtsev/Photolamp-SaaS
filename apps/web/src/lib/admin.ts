import { z } from "zod";

export const membershipSchema = z.object({
  tenant_id: z.string(),
  slug: z.string(),
  display_name: z.string(),
  timezone: z.string(),
  role: z.enum(["owner", "admin", "operator", "courier"]),
});

const branchSchema = z.object({
  id: z.string(),
  name: z.string(),
  city: z.string(),
  timezone: z.string(),
  pickup_enabled: z.boolean(),
  delivery_enabled: z.boolean(),
  state: z.enum(["active", "inactive"]),
});

const modelSchema = z.object({
  id: z.string(),
  name: z.string(),
  manufacturer: z.string().nullable(),
  description: z.string(),
  included_items: z.array(z.string()),
  illustration: z.enum(["floor", "compact", "mobile"]),
  publication_state: z.enum(["draft", "published", "archived"]),
});

const assetSchema = z.object({
  id: z.string(),
  branch_id: z.string(),
  branch_name: z.string(),
  equipment_model_id: z.string(),
  model_name: z.string(),
  asset_code: z.string(),
  serial_no: z.string().nullable(),
  state: z.enum(["available", "rented", "maintenance", "inspection", "retired"]),
});

const pricingSchema = z.object({
  id: z.string(),
  equipment_model_id: z.string(),
  model_name: z.string(),
  amount_minor_per_day: z.number().int(),
  deposit_minor: z.number().int(),
  currency: z.literal("RUB"),
  minimum_days: z.number().int(),
  maximum_days: z.number().int(),
  state: z.enum(["draft", "active", "retired"]),
  is_demo: z.boolean(),
});

export const adminCatalogSchema = z.object({
  tenant_id: z.string(),
  role: z.enum(["owner", "admin", "operator", "courier"]),
  branches: z.array(branchSchema),
  models: z.array(modelSchema),
  assets: z.array(assetSchema),
  pricing: z.array(pricingSchema),
});

export type Membership = z.infer<typeof membershipSchema>;
export type AdminCatalog = z.infer<typeof adminCatalogSchema>;
