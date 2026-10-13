# ADR 0006: атомарный rental hold

Статус: принято. Дата: 2026-10-13.

## Контекст

Availability и quote читают актуальные данные, но между проверкой и созданием
заказа другой запрос мог выбрать тот же физический прибор. До сбора данных
клиента нужен короткий tenant-scoped hold, который выдерживает конкурентные
запросы и освобождает прибор после тайм-аута.

## Решение

- `rental_allocations` хранит назначенный asset, диапазон включительных дат,
  состояние, срок hold и снимок серверного quote. Таблица защищена composite FK,
  ENABLE/FORCE RLS и runtime-grant только на select/insert/update.
- PostgreSQL exclusion constraint на `tenant_id + asset_id + daterange` запрещает
  пересечение записей в состояниях `held` и `confirmed`. `FOR UPDATE SKIP LOCKED`
  выбирает свободный прибор без общей блокировки инвентаря; constraint остаётся
  последней защитой от двойного назначения.
- `POST /api/v1/r/{tenant_slug}/checkout/holds` требует `Idempotency-Key` и
  capability `X-Checkout-Token` из анонимной checkout-сессии.
  Сервер хранит только SHA-256 ключа. Transaction advisory lock делает
  одновременный повтор того же запроса идемпотентным, а повтор с иным payload
  возвращает 409.
- Успешный ответ содержит публичный quote, UUID hold, TTL и случайный bearer-токен
  управления. Asset ID, инвентарный код и серийный номер не раскрываются. При
  повторе токен перевыпускается, в базе хранится только его SHA-256.
- `DELETE /api/v1/r/{tenant_slug}/checkout/holds/{hold_id}` требует
  `X-Checkout-Token` и `X-Hold-Token`, атомарно переводит hold в `released` и
  идемпотентен для уже освобождённой записи с теми же capabilities.
- TTL задаётся `HOLD_TTL_SECONDS` в диапазоне 60–1800 секунд, по умолчанию 600.
  Availability сразу игнорирует истёкший hold; следующий create/release в tenant
  переводит такие записи в `expired` перед выделением asset.
- Создание hold разрешено только когда у витрины явно установлен
  `booking_enabled=true`. В текущих demo seed это значение false, поэтому UI и
  публичный staging не создают резервирования.

## Продолжение

Анонимная checkout capability, rate limiting и атомарное создание истекающего
order из hold реализованы в ADR 0007. Этот order остаётся черновиком: нет
подтверждённой личности, SMS, оферты, оплаты или перехода в `confirmed`. Landing
продолжает показывать quote с выключенной кнопкой оформления.

## Проверки

Интеграционные тесты на PostgreSQL под runtime-ролью запускают два параллельных
запроса на единственный asset и требуют ровно один успех. Дополнительно проверены
RLS без tenant scope, cross-tenant composite FK, выключенный storefront,
идемпотентный replay с ротацией токена, освобождение, повторное освобождение и
возврат истёкшего hold в доступность.
