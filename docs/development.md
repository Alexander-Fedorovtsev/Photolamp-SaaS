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

GitHub Actions: frontend lint/format/types/unit/build/audit; backend lint/format/
mypy/unit/integration/coverage и reversible migration; stack Docker build + real
smoke + Playwright/axe + остановка Redis и восстановление. `quality-gate`
успешен только при успехе frontend, backend, stack и infrastructure jobs. Последний
проверяет CD gate, staging topology и настоящий Traefik/HTTPS/Basic Auth. Для защиты main можно назначить
`quality-gate` required check в правилах репозитория.

Предусмотрены только foundation endpoints:

| Endpoint | Поведение |
| --- | --- |
| API `/api/v1/health/live` | 200 при живом процессе |
| API `/api/v1/health/ready` | 200 после PostgreSQL migration + Redis PING; иначе 503 |
| API `/api/v1/r/{slug}/preview` | Публичный synthetic demo DTO, либо 404 |
| API `/api/v1/me/tenants` | Активные memberships непрозрачной сессии; 401 без неё |
| API `/api/v1/t/{tenant_id}/context` | Проверенный tenant context под RLS; 404 для чужого tenant |
| API `/docs`, `/openapi.json` | Реальная спецификация текущего API |
| Web `/api/health/live` | 200 при живом web |
| Web `/api/health/ready` | 200 при готовом API; иначе 503 |
| API `/api/v1/version` | SHA текущего API-образа, no-store |
| Web `/api/version` | SHA web/API при совпадении, иначе 503; no-store |

План и границы SaaS: `architecture/adr-0001-foundation.md`.
Реализованная tenant/RLS-модель: `architecture/adr-0002-tenant-identity-rls.md`.
Подготовка следующего landing PR: `product/landing-foundation.md`.
Публикация staging и GitHub CD: `deployment.md`.
