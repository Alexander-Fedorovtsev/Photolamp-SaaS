# ADR 0007: анонимная checkout-сессия и черновик заказа

Статус: принято. Дата: 2026-10-13.

## Контекст

Атомарный hold защищает конкретный прибор от двойного назначения, но сам по себе
не задаёт границу одного браузерного checkout и не создаёт запись, которую позже
можно безопасно продолжить после подтверждения личности. При этом текущий этап не
должен собирать контакты или изображать готовые SMS и платёжные интеграции.

## Решение

- `checkout_sessions` хранит tenant-scoped анонимную capability-сессию с TTL.
  Клиент получает непрозрачный токен один раз; PostgreSQL хранит только SHA-256.
  Сессия явно возвращает `identity_verified=false` и не содержит PII.
- Hold теперь связан с checkout-сессией и требует `X-Checkout-Token`. Управлять
  hold можно только сочетанием checkout capability и отдельного `X-Hold-Token`.
- `POST /api/v1/r/{tenant_slug}/checkout/orders` атомарно создаёт из активного
  hold один order в состоянии `held`. Он наследует даты, способ получения, TTL и
  серверный quote snapshot; поля цены и asset из браузера не принимаются.
- Создание order требует `Idempotency-Key`. Transaction advisory lock и unique
  constraints обеспечивают один результат при конкурентном повторе. В
  `order_events` записывается ровно одно событие `draft_created`.
- Истечение hold переводит связанный order в `expired`, освобождение — в
  `cancelled`. Order нельзя подтвердить или оплатить через текущий API.
- `checkout_sessions`, `orders` и `order_events` используют tenant composite FK,
  ENABLE/FORCE RLS и минимальные grants runtime-роли. Публичный DTO не раскрывает
  asset ID, инвентарный код или внутренние события.
- Redis ограничивает выдачу сессий по витрине и операции по хэшу capability.
  Проверка fail closed: при недоступном Redis checkout возвращает 503. Параметры
  TTL и лимитов задаются окружением и имеют безопасные диапазоны.
- API доступен только у витрины с `booking_enabled=true`. У demo-витрин флаг
  остаётся false, поэтому landing не показывает работающую кнопку заказа.

## Граница этапа

Анонимная capability не подтверждает человека. Order в состоянии `held` —
истекающий черновик, а не подтверждённая аренда. Клиентские имя, телефон, адрес,
согласия и платёжные данные не принимаются. SMS/OTP, переход в `confirmed`,
уведомления, договор и оплата отсутствуют.

Следующий этап должен добавить подтверждённую клиентскую identity через реального
провайдера SMS/OTP, явные согласия и state transition `held → confirmed` с
аудитом. После выбора провайдера нужны отдельные секреты, webhook contract,
anti-abuse политика и сценарий восстановления; до этого UI checkout остаётся
заблокированным.

## Проверки

Интеграционные тесты на PostgreSQL и Redis проверяют конкурентный idempotent
create, приватность capability, один audit event, RLS без tenant scope, 429 с
`Retry-After`, истечение hold/order, отмену при release и запрет повторного order
для одного hold. OpenAPI фиксирует `identity_verified=false`,
`payment_status=not_started` и `confirmation_available=false`.
