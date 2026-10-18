export function validateRentalDates(
  start: string,
  end: string,
  minimum: number,
  maximum: number,
  timeZone: string,
  now = new Date(),
): { code: string; field: string; minimum_days?: number; maximum_days?: number } | null {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const value = (type: string) => parts.find((part) => part.type === type)?.value;
  const today = `${value("year")}-${value("month")}-${value("day")}`;
  if (start < today) return { code: "DATE_IN_PAST", field: "start_date" };
  const days = (Date.parse(end) - Date.parse(start)) / 86400000 + 1;
  if (days < 1) return { code: "END_BEFORE_START", field: "end_date" };
  if (days < minimum) return { code: "MINIMUM_DAYS", field: "end_date", minimum_days: minimum };
  if (days > maximum) return { code: "MAXIMUM_DAYS", field: "end_date", maximum_days: maximum };
  return null;
}
