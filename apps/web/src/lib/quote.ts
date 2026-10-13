import { z } from "zod";

const uuid = z.string().regex(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);

export const quoteRequestSchema = z
  .object({
    model_id: uuid,
    branch_id: uuid,
    start_date: isoDate,
    end_date: isoDate,
    fulfillment: z.enum(["pickup", "delivery"]),
  })
  .strict();

export const quoteSchema = z
  .object({
    model_id: uuid,
    branch_id: uuid,
    start_date: isoDate,
    end_date: isoDate,
    days: z.number().int().min(1).max(365),
    available_units: z.number().int().nonnegative(),
    available: z.literal(true),
    availability_confirmed: z.literal(true),
    reservation_created: z.literal(false),
    fulfillment: z.enum(["pickup", "delivery"]),
    amount_minor_per_day: z.number().int().positive(),
    rental_amount_minor: z.number().int().positive(),
    deposit_minor: z.number().int().nonnegative(),
    delivery_amount_minor: z.literal(0),
    total_minor: z.number().int().positive(),
    currency: z.literal("RUB"),
    price_is_demo: z.boolean(),
    booking_enabled: z.literal(false),
  })
  .refine((quote) => quote.rental_amount_minor === quote.amount_minor_per_day * quote.days)
  .refine(
    (quote) =>
      quote.total_minor ===
      quote.rental_amount_minor + quote.deposit_minor + quote.delivery_amount_minor,
  );

export type Quote = z.infer<typeof quoteSchema>;
