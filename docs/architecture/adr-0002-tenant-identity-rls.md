# ADR 0002: tenant identity, opaque sessions и PostgreSQL RLS

Статус: принято. Дата: 2026-10-13.

## Контекст

Первые приватные SaaS-сущности требуют двух независимых границ: API проверяет
сессию и активное членство, PostgreSQL ограничивает каждую транзакцию выбранной
организацией. Заголовок, slug или UUID из URL сами по себе не дают доступ.

## Решение

- `users` — глобальная учётная запись; сейчас хранится только E.164 телефон,
  состояние и время создания. В репозиторий попадают лишь синтетические номера.
- `tenants` — организация; `tenant_memberships` связывает пользователя с tenant
  и ролью `owner/admin/operator/courier`.
- `auth_sessions` хранит SHA-256 непрозрачного случайного bearer token, срок и
  отзыв. API никогда не ищет сессию по открытому token в таблице.
- Три узкие `SECURITY DEFINER` функции с фиксированным `pg_catalog` search path
  проверяют hash сессии, срок, пользователя, membership и состояние tenant.
  Public execute отозван. Функции не принимают `user_id`, поэтому вызывающий не
  может запросить членства другого пользователя.
- После проверки membership API вызывает transaction-local
  `set_config('app.tenant_id', ..., true)`. `tenants` и `tenant_memberships`
  используют `ENABLE/FORCE ROW LEVEL SECURITY`, одинаковые `USING/WITH CHECK` и
  deny-by-default при отсутствии scope. Пул не переносит scope между транзакциями.
- API подключается только как `photolamp_runtime`: `NOSUPERUSER`, `NOCREATEDB`,
  `NOCREATEROLE`, `NOINHERIT`, `NOBYPASSRLS`, без доступа к `users` и
  `auth_sessions`. Owner URL получают только bootstrap и Alembic процессы.
  Runtime password отдельный и автоматически создаётся в server `.env` при первом
  CD без печати значения; bootstrap создаёт роль или синхронизирует пароль.

## Текущий HTTP-контракт

- `GET /api/v1/me/tenants` принимает непрозрачную сессию и возвращает только
  активные членства этой сессии.
- `GET /api/v1/t/{tenant_id}/context` повторно проверяет членство, устанавливает
  RLS scope внутри одной транзакции и возвращает безопасный контекст.
- Неизвестная/просроченная сессия — `401`; чужой tenant — `404`.
  `X-Tenant-ID` игнорируется и не участвует в авторизации.

Сейчас нет публичного способа выпустить сессию. Реальные SMS не подключены,
staging не содержит пользователей и персональных данных. OTP request/verify,
rate limits, HttpOnly cookie, CSRF и отзыв сессий через UI входят в следующий
auth PR. Текущие endpoints нужны для проверки модели доступа и будущих сервисов.

## Проверки

Integration-тесты работают под настоящей runtime ролью PostgreSQL и проверяют:
отсутствие данных без scope, cross-tenant SELECT/UPDATE/DELETE/INSERT, запрет
чтения global identity/session таблиц, флаги роли, сброс scope при повторном
использовании соединения, URL/header tampering и две независимые сессии.

Composite tenant foreign keys появятся с первой дочерней доменной сущностью
(инвентарь): у текущего membership tenant является самим родителем, поэтому
составная ссылка пока не имеет второго scoped identifier.
