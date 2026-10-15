import { z } from "zod";
import { quoteSchema } from "./quote";

const uuidSchema = z
  .string()
  .regex(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
const isoDateSchema = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const isoDateTimeSchema = z.string().datetime({ offset: true });

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

const assetBlockSchema = z.object({
  id: z.string(),
  asset_id: z.string(),
  asset_code: z.string(),
  start_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  end_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  reason: z.enum(["maintenance", "inspection", "manual"]),
  note: z.string().nullable(),
});

export const adminCatalogSchema = z.object({
  tenant_id: z.string(),
  role: z.enum(["owner", "admin", "operator", "courier"]),
  branches: z.array(branchSchema),
  models: z.array(modelSchema),
  assets: z.array(assetSchema),
  pricing: z.array(pricingSchema),
  asset_blocks: z.array(assetBlockSchema).default([]),
});

export const adminOrderStateSchema = z.enum([
  "held",
  "confirmed",
  "in_progress",
  "returned",
  "completed",
  "expired",
  "cancelled",
]);

const adminOrderSnapshotSchema = z
  .object({
    schema_version: z.literal(1),
    model_id: uuidSchema,
    model_name: z.string().min(1),
    branch_id: uuidSchema,
    branch_name: z.string().min(1),
    start_date: isoDateSchema,
    end_date: isoDateSchema,
    fulfillment: z.enum(["pickup", "delivery"]),
    quote: quoteSchema,
  })
  .strict();

const extensionRequestSchema = z
  .object({
    request_id: uuidSchema,
    state: z.enum(["pending", "approved", "rejected"]),
    original_end_date: isoDateSchema,
    requested_end_date: isoDateSchema,
    additional_days: z.number().int().positive(),
    amount_minor: z.number().int().positive(),
    currency: z.literal("RUB"),
    requested_at: isoDateTimeSchema,
    decided_at: isoDateTimeSchema.nullable(),
    rejection_reason: z.enum(["availability_conflict", "manual_review"]).nullable(),
  })
  .strict();

const returnRequestSchema = z
  .object({
    request_id: uuidSchema,
    state: z.enum(["pending", "acknowledged", "rejected"]),
    requested_date: isoDateSchema,
    requested_at: isoDateTimeSchema,
    decided_at: isoDateTimeSchema.nullable(),
    rejection_reason: z.enum(["date_unavailable", "manual_review"]).nullable(),
  })
  .strict();

export const adminOrderSchema = z
  .object({
    order_id: uuidSchema,
    state: adminOrderStateSchema,
    source: z.enum(["website", "phone"]),
    snapshot: adminOrderSnapshotSchema,
    planned_end_date: isoDateSchema,
    is_overdue: z.boolean(),
    overdue_days: z.number().int().nonnegative(),
    extension_request: extensionRequestSchema.nullable(),
    return_request: returnRequestSchema.nullable(),
    identity_verified: z.boolean(),
    payment_status: z.literal("not_started"),
    expires_at: isoDateTimeSchema.nullable(),
    confirmed_at: isoDateTimeSchema.nullable(),
    handed_over_at: isoDateTimeSchema.nullable(),
    returned_at: isoDateTimeSchema.nullable(),
    completed_at: isoDateTimeSchema.nullable(),
    inspection_outcome: z.enum(["passed", "maintenance_required"]).nullable(),
    created_at: isoDateTimeSchema,
    updated_at: isoDateTimeSchema,
  })
  .strict();

export const adminOrderPageSchema = z
  .object({
    items: z.array(adminOrderSchema),
    total: z.number().int().nonnegative(),
    limit: z.number().int().min(1).max(100),
    offset: z.number().int().nonnegative(),
  })
  .strict();

export const adminOrderTransitionSchema = z
  .object({
    order: adminOrderSchema,
    replayed: z.boolean(),
  })
  .strict();

export const adminExtensionDecisionSchema = z
  .object({
    order: adminOrderSchema,
    request: extensionRequestSchema,
    replayed: z.boolean(),
  })
  .strict();

export const adminReturnDecisionSchema = z
  .object({
    order: adminOrderSchema,
    request: returnRequestSchema,
    replayed: z.boolean(),
  })
  .strict();

export const adminOrderContactAccessSchema = z
  .object({
    contact: z
      .object({
        phone_e164: z.string().regex(/^\+[1-9][0-9]{7,14}$/),
        recipient_name: z.string().min(1).max(120),
        delivery_address: z.string().min(1).max(500).nullable(),
      })
      .strict(),
    replayed: z.boolean(),
  })
  .strict();

const dashboardOrderSchema = z
  .object({
    order_id: uuidSchema,
    state: adminOrderStateSchema,
    source: z.enum(["website", "phone"]),
    model_name: z.string().min(1),
    branch_name: z.string().min(1),
    start_date: isoDateSchema,
    end_date: isoDateSchema,
    fulfillment: z.enum(["pickup", "delivery"]),
    overdue_days: z.number().int().nonnegative(),
    pending_request_types: z.array(z.enum(["extension", "return"])),
  })
  .strict();

export const operationsDashboardSchema = z
  .object({
    tenant_id: uuidSchema,
    timezone: z.string().min(1),
    local_date: isoDateSchema,
    generated_at: isoDateTimeSchema,
    counts: z
      .object({
        awaiting_confirmation: z.number().int().nonnegative(),
        handovers_due: z.number().int().nonnegative(),
        returns_today: z.number().int().nonnegative(),
        returns_tomorrow: z.number().int().nonnegative(),
        overdue: z.number().int().nonnegative(),
        inspection: z.number().int().nonnegative(),
        pending_requests: z.number().int().nonnegative(),
      })
      .strict(),
    inventory: z
      .object({
        available: z.number().int().nonnegative(),
        rented: z.number().int().nonnegative(),
        inspection: z.number().int().nonnegative(),
        maintenance: z.number().int().nonnegative(),
        retired: z.number().int().nonnegative(),
      })
      .strict(),
    handovers_due: z.array(dashboardOrderSchema),
    returns_today: z.array(dashboardOrderSchema),
    returns_tomorrow: z.array(dashboardOrderSchema),
    overdue: z.array(dashboardOrderSchema),
    pending_requests: z.array(dashboardOrderSchema),
  })
  .strict();

export const staffMemberSchema = z
  .object({
    user_id: uuidSchema,
    display_name: z.string().min(1).max(120),
    role: z.enum(["owner", "admin", "operator", "courier"]),
    state: z.enum(["invited", "active", "disabled"]),
    is_current_user: z.boolean(),
    created_at: isoDateTimeSchema,
    updated_at: isoDateTimeSchema,
  })
  .strict();

export const teamSnapshotSchema = z
  .object({
    tenant_id: uuidSchema,
    can_manage: z.boolean(),
    members: z.array(staffMemberSchema),
  })
  .strict();

export const staffInvitationSummarySchema = z
  .object({
    invitation_id: uuidSchema,
    user_id: uuidSchema,
    state: z.enum(["pending", "expired", "accepted", "revoked"]),
    expires_at: isoDateTimeSchema,
    created_at: isoDateTimeSchema,
  })
  .strict();

export const staffInvitationCreatedSchema = z
  .object({
    invitation_id: uuidSchema,
    invitation_token: z.string().regex(/^pli_[A-Za-z0-9_-]{32,128}$/),
    expires_at: isoDateTimeSchema,
    member: staffMemberSchema,
  })
  .strict();

export const staffSessionSummarySchema = z
  .object({
    session_id: uuidSchema,
    client_label: z.string().min(1).max(80),
    created_at: isoDateTimeSchema,
    last_seen_at: isoDateTimeSchema,
    expires_at: isoDateTimeSchema,
    is_current: z.boolean(),
  })
  .strict();

export const staffSessionRevokedSchema = z
  .object({ revoked: z.literal(true), revoked_current: z.boolean() })
  .strict();

export type Membership = z.infer<typeof membershipSchema>;
export type AdminCatalog = z.infer<typeof adminCatalogSchema>;
export type AdminOrder = z.infer<typeof adminOrderSchema>;
export type AdminOrderPage = z.infer<typeof adminOrderPageSchema>;
export type AdminOrderState = z.infer<typeof adminOrderStateSchema>;
export type AdminOrderContact = z.infer<typeof adminOrderContactAccessSchema>["contact"];
export type OperationsDashboard = z.infer<typeof operationsDashboardSchema>;
export type DashboardOrder = z.infer<typeof dashboardOrderSchema>;
export type StaffMember = z.infer<typeof staffMemberSchema>;
export type TeamSnapshot = z.infer<typeof teamSnapshotSchema>;
export type StaffInvitationSummary = z.infer<typeof staffInvitationSummarySchema>;
export type StaffInvitationCreated = z.infer<typeof staffInvitationCreatedSchema>;
export type StaffSessionSummary = z.infer<typeof staffSessionSummarySchema>;
