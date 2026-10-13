import { describe, expect, it } from "vitest";
import { quoteRequestSchema, quoteSchema } from "./quote";

const request = {
  model_id: "d2000000-0000-0000-0000-000000000001",
  branch_id: "d1000000-0000-0000-0000-000000000001",
  start_date: "2030-01-10",
  end_date: "2030-01-12",
  fulfillment: "delivery",
} as const;

const quote = {
  ...request,
  days: 3,
  available_units: 1,
  available: true,
  availability_confirmed: true,
  reservation_created: false,
  amount_minor_per_day: 49_000,
  rental_amount_minor: 147_000,
  deposit_minor: 0,
  delivery_amount_minor: 0,
  total_minor: 147_000,
  currency: "RUB",
  price_is_demo: true,
  booking_enabled: false,
} as const;

describe("quote contract", () => {
  it("accepts a server-calculated quote", () => {
    expect(quoteSchema.parse(quote).total_minor).toBe(147_000);
  });

  it("rejects client prices and inconsistent server totals", () => {
    expect(() => quoteRequestSchema.parse({ ...request, rental_amount_minor: 1 })).toThrow();
    expect(() => quoteSchema.parse({ ...quote, rental_amount_minor: 1, total_minor: 1 })).toThrow();
  });
});
