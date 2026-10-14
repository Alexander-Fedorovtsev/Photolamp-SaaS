# Локальная разработка и проверки

## Полный стек в Docker

Нужны Git, Docker Engine с Compose v2 и свободные порты 3000/8000/5432/6379.
Из корня репозитория:

```bash
cp .env.example .env
docker compose config --quiet
docker compose up --build --wait --wait-timeout 180
python3 tests/e2e/smoke.py
```

Откройте `http://localhost:3000`, демо `http://localhost:3000/r/demo-ufa` и
`http://localhost:3000/r/demo-kazan`, API docs `http://localhost:8000/docs`.
Compose создаёт/обновляет отдельную runtime-роль, затем запускает миграцию до API.
База в named volume сохраняется при остановке;
Redis без persistence, поскольку фоновые задачи ещё отсутствуют.
`docker compose logs api web migrate` помогает диагностировать запуск.
`docker compose down` останавливает стек; `docker compose down --volumes`
**удаляет локальную базу** и применяется только для сброса демо-окружения.

Локальный Compose включает `SMS_PROVIDER_MODE=memory` только для разработки.
Adapter не отправляет SMS и не хранит номер; OTP доступен лишь тестовому процессу.
Staging жёстко использует `disabled`, поэтому нельзя считать SMS настроенным по
наличию endpoint в OpenAPI. Не вводите реальные номера в dev/staging.
Handover, продление и решения по возврату создают записи `notification_jobs`, но при
`disabled` они остаются `blocked/provider_disabled`. Worker и попытки доставки не
запускаются; эти записи нельзя трактовать как отправленные сообщения.

Staff-приглашения не зависят от SMS: owner вручную передаёт одноразовую ссылку.
`STAFF_INVITATION_TTL_SECONDS` задаёт срок ссылки (по умолчанию 48 часов),
`STAFF_SESSION_TTL_SECONDS` — срок staff-сессии (12 часов), а
`STAFF_AUTH_RATE_LIMIT` — число попыток входа за минуту для одного логина.

`CONTACT_ENCRYPTION_KEY` — URL-safe base64 от 32 случайных байт. Локальный и
staging demo используют документированный синтетический ключ только потому, что
API demo-tenant принимает исключительно `+70000000000`, имя `Тестовый клиент` и
адрес `Тестовый адрес`. Перед включением реальных контактов задайте уникальные
`CONTACT_ENCRYPTION_KEY` и `OTP_PEPPER`; production отклоняет синтетические
значения при старте. Сгенерировать ключ можно без вывода других секретов:

```bash
python -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
openssl rand -hex 32
```

Первая команда — для `CONTACT_ENCRYPTION_KEY`, вторая — для `OTP_PEPPER`.
Смена encryption key требует отдельной ротации сохранённых ciphertext, поэтому
нельзя просто заменить его в окружении с реальными данными.

Пароль в `.env.example` — только local development. Если меняете пароль после
первого запуска Postgres, volume хранит предыдущие credentials: обновите пароль
в БД или сбросьте только ненужную локальную базу. В пароле для interpolated
DATABASE_URL используйте URI-safe символы либо корректное URL-кодирование.

## Native development с hot reload

Нужны Node.js 22.12+ (CI/Docker: Node 22), Python 3.12 и uv 0.12.23.
Зависимости зафиксированы lock-файлами. Из корня:

```bash
cp .env.example .env
docker compose up -d postgres redis
```

API (отдельный терминал):

```bash
cd apps/api
uv sync --frozen
uv run --env-file ../../.env python -m app.bootstrap_runtime_role
uv run --env-file ../../.env alembic upgrade head
uv run --env-file ../../.env uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Web (другой терминал):

```bash
cd apps/web
npm ci
npm run dev
```

Web по умолчанию обращается к `http://localhost:8000` на сервере; для другого
адреса задайте `API_INTERNAL_URL` в окружении или `apps/web/.env.local`.
Браузер не получает внутренний адрес API. CORS не требуется: API-вызовы идут
через server components и server routes, cross-origin browser API пока нет.
Не запускайте native API/web одновременно с контейнерами на тех же портах.

## Проверки frontend

```bash
cd apps/web
npm ci
npm run lint
npm run format:check
npm run typecheck
npm test
npm run build
npm audit --omit=dev --audit-level=high
npx playwright install --with-deps chromium
npm run test:e2e
```

E2E требует запущенного полного стека с demo previews и применённой миграцией.
`E2E_BASE_URL` меняет адрес web. Playwright запускается на 320/430/1440 px,
проверяет навигацию, разные бренды, 404, blocked checkout, readiness, keyboard,
axe WCAG A/AA, reduced-motion и manifest. При наличии системного Chromium можно
задать `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium` вместо скачивания.

## Проверки backend

```bash
cd apps/api
uv sync --frozen
uv export --frozen --no-dev --format requirements-txt --output-file /tmp/photolamp-api-runtime.txt
uvx --from pip-audit==2.10.1 pip-audit --requirement /tmp/photolamp-api-runtime.txt
uv run ruff check .
uv run ruff format --check .
uv run mypy app migrations tests
uv run --env-file ../../.env alembic upgrade head
uv run --env-file ../../.env pytest --cov=app --cov-report=term-missing --cov-fail-under=85
```

Unit-only, без запущенных зависимостей: `uv run pytest -m 'not integration'`.
Тесты с marker `integration` требуют настоящие PostgreSQL и Redis.
Проверка обратимости baseline в одноразовой dev-БД:
`uv run --env-file ../../.env alembic downgrade base`, затем снова `upgrade head`.
При downgrade readiness ожидаемо возвращает 503 до повторной миграции.
Не выполнять downgrade в окружении с важными данными без отдельного плана.

## Облачная среда Codex

В среде с HTTPS proxy и собственной CA используйте BuildKit secret override:

```bash
docker compose -f compose.yaml -f infra/compose.cloud.yaml build
docker compose up --wait --wait-timeout 180
```

Override использует публичный certificate bundle из `CODEX_PROXY_CERT`, либо
`/etc/ssl/certs/ca-certificates.crt`. CA монтируется только на время сетевых build
steps и не сохраняется в образах. TLS verification остаётся включённой.
Proxy-настройки Docker управляются облачной средой; секреты не копируются в код.

## CI и дальнейшая работа

GitHub Actions: frontend lint/format/types/unit/build/audit; backend locked runtime
dependency audit, lint/format/mypy/unit/integration/coverage и reversible migration;
stack Docker build + real
smoke + Playwright/axe + остановка Redis и восстановление. `quality-gate`
успешен только при успехе frontend, backend, stack и infrastructure jobs. Последний
проверяет CD gate, staging topology и настоящий Traefik/HTTPS/Basic Auth. Для защиты main можно назначить
`quality-gate` required check в правилах репозитория.

Предусмотрены только foundation endpoints:

| Endpoint | Поведение |
| --- | --- |
| API `/api/v1/health/live` | 200 при живом процессе |
| API `/api/v1/health/ready` | 200 после PostgreSQL migration + Redis PING; иначе 503 |
| API `/api/v1/r/{slug}/catalog` | DB-backed публичный demo-каталог без приватных asset-полей, либо 404 |
| API `/api/v1/r/{slug}/preview` | Deprecated summary того же DB-backed каталога |
| API `/api/v1/me/tenants` | Активные memberships непрозрачной сессии; 401 без неё |
| API `/api/v1/t/{tenant_id}/context` | Проверенный tenant context под RLS; 404 для чужого tenant |
| API `POST /api/v1/auth/demo-session` | Короткая синтетическая staff session только при demo-режиме; production возвращает 404 |
| API `/api/v1/t/{tenant_id}/catalog-admin` | Приватная staff read-model после session + membership + RLS проверки |
| API `POST/PATCH /api/v1/t/{tenant_id}/{models,assets,pricing,branches}` | Owner/admin CRUD без доверия к tenant ID из body; изменения аудируются |
| API `/api/v1/r/{slug}/availability` | Агрегированная доступность модели на включительный диапазон дат |
| API `POST /api/v1/r/{slug}/checkout/quote` | Серверный расчёт тарифа и снимок доступности без резервирования |
| API `POST /api/v1/r/{slug}/checkout/sessions` | Короткая анонимная capability без PII только для явно включённой витрины |
| API `POST /api/v1/r/{slug}/checkout/holds` | Атомарный hold по `Idempotency-Key` + `X-Checkout-Token` |
| API `DELETE /api/v1/r/{slug}/checkout/holds/{id}` | Освобождение hold по `X-Checkout-Token` + `X-Hold-Token` |
| API `POST /api/v1/r/{slug}/checkout/orders` | Истекающий idempotent draft из активного hold; не подтверждает аренду |
| API `GET /api/v1/r/{slug}/checkout/orders/{id}` | Активный draft только владельцу checkout capability |
| API `POST /api/v1/r/{slug}/checkout/identity/otp/request` | OTP challenge; работает только с явно настроенным provider, staging возвращает 503 |
| API `POST /api/v1/r/{slug}/checkout/identity/otp/verify` | Проверка TTL/attempts и выдача opaque customer session |
| API `GET /api/v1/r/{slug}/me` | Privacy-safe customer identity по bearer session |
| API `GET /api/v1/r/{slug}/me/orders` | Ограниченная пагинацией история только владельца customer session |
| API `GET /api/v1/r/{slug}/me/orders/{id}` | Privacy-safe карточка собственного заказа во всех текущих состояниях |
| API `POST /api/v1/r/{slug}/me/orders/{id}/extension-requests` | Идемпотентный запрос продления только владельцем активного заказа |
| API `POST /api/v1/r/{slug}/me/orders/{id}/pickup-requests` | Идемпотентный запрос даты возврата только владельцем активного заказа |
| API `GET /api/v1/t/{tenant_id}/orders` | Tenant-scoped очередь owner/admin/operator без открытых контактов |
| API `GET /api/v1/t/{tenant_id}/dashboard` | Privacy-safe сводка owner/admin/operator по срокам филиалов, запросам и состоянию парка |
| API `GET /api/v1/t/{tenant_id}/team` | Privacy-safe список memberships для owner/admin без телефонов и session data |
| API `PATCH /api/v1/t/{tenant_id}/team/{user_id}` | Owner-only изменение роли/состояния/подписи с аудитом и защитой active owner |
| API `POST/GET /api/v1/t/{tenant_id}/team/invitations` | Owner создаёт одноразовую ссылку; повторный GET возвращает только безопасные metadata без token |
| API `DELETE /api/v1/t/{tenant_id}/team/invitations/{invitation_id}` | Owner отзывает неиспользованное приглашение и блокирует invited membership |
| API `POST /api/v1/auth/staff/invitations/redeem` | Атомарная активация ссылки, выбор login/scrypt password и выдача opaque session |
| API `POST /api/v1/auth/staff/login` | Повторный staff login с generic errors, rate limit и временной блокировкой |
| API `GET /api/v1/auth/staff/sessions` | Privacy-safe список собственных активных сессий без IP, полного User-Agent и token hash |
| API `DELETE /api/v1/auth/staff/sessions/{session_id}` | Отзыв одной принадлежащей identity сессии; чужая сессия выглядит как 404 |
| API `DELETE /api/v1/auth/staff/sessions` | Атомарный отзыв всех password-сессий identity, включая текущую |
| API `POST /api/v1/t/{tenant_id}/orders/phone` | Атомарный подтверждённый заказ оператора; контакты и адрес шифруются, demo принимает только фиксированный synthetic contact |
| API `POST /api/v1/t/{tenant_id}/orders/{id}/{confirm,cancel}` | Idempotent staff-переход с actor audit и синхронным состоянием allocation |
| API `POST /api/v1/t/{tenant_id}/orders/{id}/extension-requests/{request_id}/{approve,reject}` | Staff-решение с повторной проверкой availability и сохранением исходного snapshot |
| API `POST /api/v1/t/{tenant_id}/orders/{id}/pickup-requests/{request_id}/{acknowledge,reject}` | Staff-решение по запросу возврата с аудитом |
| API `POST /api/v1/t/{tenant_id}/assets/{asset_id}/blocks` | Tenant-scoped сервисная блокировка прибора с аудитом |
| API `/docs`, `/openapi.json` | Реальная спецификация текущего API |
| Web `/admin` | Staff login и адаптивная tenant-панель через HttpOnly session proxy; demo-вход отдельно отключается в production |
| Web `/admin/invite#token=…` | Клиентская активация одноразовой ссылки; fragment очищается до отправки формы |
| Web `/api/admin/sessions[/{id}]` | Same-origin BFF для списка и отзыва с удалением cookie текущей сессии |
| Web `/api/admin/*` mutations | Next.js proxy требует совпадающий `Origin`; staging/production staff cookie всегда `Secure` |
| Web `/api/health/live` | 200 при живом web |
| Web `/api/health/ready` | 200 при готовом API; иначе 503 |
| API `/api/v1/version` | SHA текущего API-образа, no-store |
| Web `/api/version` | SHA web/API при совпадении, иначе 503; no-store |

План и границы SaaS: `architecture/adr-0001-foundation.md`.
Реализованная tenant/RLS-модель: `architecture/adr-0002-tenant-identity-rls.md`.
Каталог и публичная read-model: `architecture/adr-0003-catalog-inventory.md`.
Demo staff admin и аудит: `architecture/adr-0004-demo-staff-admin.md`.
Availability и серверный quote: `architecture/adr-0005-availability-quote.md`.
Атомарные rental holds: `architecture/adr-0006-atomic-rental-holds.md`.
Анонимный checkout и draft orders: `architecture/adr-0007-anonymous-checkout-drafts.md`.
Customer OTP/provider boundary: `architecture/adr-0008-customer-otp-boundary.md`.
Customer order read model: `architecture/adr-0009-customer-order-read-model.md`.
Staff order operations: `architecture/adr-0010-staff-order-operations.md`.
Encrypted phone orders: `architecture/adr-0011-encrypted-phone-orders.md`.
Fulfillment lifecycle: `architecture/adr-0012-order-fulfillment.md`.
Extensions, overdue and notification outbox: `architecture/adr-0013-order-requests-notifications.md`.
Tenant operations dashboard: `architecture/adr-0014-operations-dashboard.md`.
Tenant staff directory and role controls: `architecture/adr-0015-tenant-staff-access.md`.
Staff invitations and password authentication: `architecture/adr-0016-staff-invitations-auth.md`.
Staff session inventory and revocation: `architecture/adr-0017-staff-session-management.md`.
Production web and dependency security baseline: `architecture/adr-0018-production-security-baseline.md`.
Реализованный landing: `product/landing-foundation.md`.
Публикация staging и GitHub CD: `deployment.md`.
