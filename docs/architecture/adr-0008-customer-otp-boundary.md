# ADR 0008: клиентская OTP identity и граница SMS-провайдера

Статус: принято. Дата: 2026-10-13.

## Контекст

Анонимная checkout capability позволяет безопасно создать hold и draft order, но
не подтверждает человека. Подключать вымышленную отправку SMS на staging нельзя,
а выбор боевого провайдера и инфраструктуры для российских персональных данных
ещё не утверждён.

## Решение

- `SmsProvider` отделён от OTP-домена. Реализованы два режима:
  `disabled` и `memory`. Memory-адаптер разрешён валидатором конфигурации только
  в `development`/`test`, не делает сетевых запросов, не сохраняет номер и
  предоставляет код только тестовому процессу. Staging принудительно использует
  `disabled` и отвечает 503 до записи challenge.
- OTP request принимает только однозначный E.164. Полный номер нужен в памяти на
  время вызова provider, но не сохраняется. `customers` и
  `customer_auth_challenges` содержат tenant-specific HMAC-SHA-256 и последние
  четыре цифры для маски. Это позволяет найти identity по повторно введённому
  номеру без plaintext PII.
- Шестизначный код генерируется CSPRNG и хранится только как HMAC от challenge ID
  и кода с application pepper. Challenge имеет TTL, ограничение попыток и
  состояния `pending`, `verified`, `expired`, `locked`, `superseded`.
- Новый OTP request аннулирует предыдущий pending challenge той же checkout
  capability. Ошибочная попытка фиксируется до возврата 422; после лимита
  challenge отвечает 429. Истёкший или заменённый challenge отвечает 409.
- Успешная проверка создаёт/находит tenant customer, привязывает customer к
  checkout session и её draft orders, затем выдаёт opaque `pc_` session. В БД
  хранится только SHA-256 токена. Идемпотентный повтор верного кода внутри TTL
  перевыпускает токен и делает предыдущий недействительным.
- `GET /api/v1/r/{tenant_slug}/me` проверяет customer bearer session и возвращает
  только UUID, маску телефона и `identity_verified=true`.
- Все три таблицы используют composite tenant FK, ENABLE/FORCE RLS и минимальные
  runtime grants. Redis ограничивает request по tenant/checkout/номеру и verify
  по tenant/checkout/challenge; scope перед записью хэшируется.

## Граница этапа

Это рабочая local/test identity, а не объявление готовой SMS-интеграции. На
публичном staging SMS отключён, demo booking остаётся выключенным. Полные номера,
имена, адреса, диагнозы и иные персональные или медицинские данные не хранятся.
Identity не переводит order в `confirmed`; `confirmation_available=false`, оплаты
и договора нет.

Для production нужно утвердить SMS-провайдера, российскую инфраструктуру и
правовые документы, реализовать сетевой adapter с delivery outbox, credential
rotation и наблюдаемостью без PII в логах. После этого можно включать UI OTP и
явный переход заказа по согласиям.

## Проверки

Интеграционные тесты на PostgreSQL и Redis проверяют disabled provider без записи,
успех и replay с ротацией session, привязку существующего order, неверный код,
lockout, supersession, expiry, RLS без tenant scope и отсутствие plaintext phone
column. Конфигурационный тест запрещает memory provider в staging/production.
