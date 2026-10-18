import { describe, expect, it } from "vitest";
import { validateRentalDates } from "./rental-dates";
import { uiError } from "./ui-error";
const now = new Date("2030-06-01T21:30:00Z");
describe("inclusive calendar days in branch timezone", () => {
  it("uses branch day at midnight and rejects past dates", () => {
    expect(
      validateRentalDates("2030-06-01", "2030-06-03", 3, 30, "Asia/Yekaterinburg", now)?.code,
    ).toBe("DATE_IN_PAST");
    expect(
      validateRentalDates("2030-06-02", "2030-06-04", 3, 30, "Asia/Yekaterinburg", now),
    ).toBeNull();
  });
  it.each([
    ["2030-06-04", "2030-06-03", "END_BEFORE_START"],
    ["2030-06-02", "2030-06-02", "MINIMUM_DAYS"],
    ["2030-06-02", "2030-07-02", "MAXIMUM_DAYS"],
  ])("explains invalid interval", (start, end, code) => {
    expect(validateRentalDates(start, end, 3, 30, "Europe/Moscow", now)?.code).toBe(code);
  });
  it("never renders an unknown upstream detail", () => {
    expect(uiError({ detail: "secret database failure" })).not.toContain("secret");
    expect(uiError({ detail: { code: "NO_UNITS_AVAILABLE" } })).toContain("Выберите другие даты");
  });
});
