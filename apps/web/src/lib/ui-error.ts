/** Allowlisted translations: upstream details must never leak arbitrary English or internals. */
export function uiError(
  payload: unknown,
  fallback = "Не удалось выполнить запрос. Обновите страницу и повторите попытку.",
): string {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  const code =
    typeof detail === "object" && detail !== null
      ? (detail as { code?: string; minimum_days?: number; maximum_days?: number })
      : null;
  switch (code?.code) {
    case "DATE_IN_PAST":
      return "Выберите первый день не раньше сегодняшнего дня в часовом поясе филиала.";
    case "END_BEFORE_START":
      return "Последний день не может быть раньше первого. Исправьте даты.";
    case "MINIMUM_DAYS":
      return `Минимальный срок — ${code.minimum_days} дн. Увеличьте период аренды.`;
    case "MAXIMUM_DAYS":
      return `Максимальный срок — ${code.maximum_days} дн. Сократите период аренды.`;
    case "NO_UNITS_AVAILABLE":
      return "На выбранные даты свободных приборов нет. Выберите другие даты.";
    case "FULFILLMENT_UNAVAILABLE":
      return "Этот способ получения недоступен в выбранном филиале. Измените способ получения.";
    case "INVALID_CONTACT_REQUEST":
      return "Не удалось открыть контакт. Обновите заказ и повторите попытку.";
  }
  const known: Record<string, string> = {
    "No units available for selected dates":
      "На выбранные даты свободных приборов нет. Выберите другие даты.",
    "Order contact details are unavailable": "Контакт недоступен на текущем этапе заказа.",
    "Owner, admin or operator role required":
      "Ваша роль не разрешает это действие. Обратитесь к владельцу.",
    "Tenant not found": "Доступ к организации изменился. Обновите рабочее пространство.",
    "Order not found": "Заказ недоступен в выбранной организации.",
  };
  if (typeof detail === "string")
    return known[detail] ?? (/^[А-ЯЁ]/.test(detail) ? detail : fallback);
  return fallback;
}

export function displayError(reason: unknown, fallback: string): string {
  return reason instanceof Error && /^[А-ЯЁ]/.test(reason.message) ? reason.message : fallback;
}
