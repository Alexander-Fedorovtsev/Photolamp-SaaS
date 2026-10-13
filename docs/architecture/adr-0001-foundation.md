# ADR 0001: foundation и границы multi-tenant SaaS

Статус: принято для этапа 0. Источники: `AGENTS.md`, `saas-tz.md`,
`docs/codex/IMPLEMENTATION_PLAN.md`. Дата: 2026-10-12.

## Решение и объём

Монорепозиторий: `apps/web` (Next.js App Router + strict TypeScript), `apps/api`
(FastAPI + SQLAlchemy async + Pydantic), `infra`, `tests/e2e`, `docs`.
Один API, PostgreSQL 16, Redis 7; никаких микросервисов. Зависимости фиксируются
в `package-lock.json` и `uv.lock`. Docker-образы API/web работают без root.

На этом этапе БД содержит только `alembic_version`. Первая миграция проверяет
работоспособность миграционного механизма, а не создаёт пустую имитацию домена.
Два искусственных публичных бренда в `app/previews.py` — fixtures для UI,
не tenants в БД. У них нет клиентов, телефонов, адресов, тарифов или доступного
checkout. API и типы frontend явно фиксируют `is_demo=true`, `booking_enabled=false`.
Preview выключен по умолчанию в API, включается в локальном Compose;
`APP_ENV=production` с demo-просмотром или локальным паролем отвергается.

Главная `/` представляет платформу; `/r/{tenantSlug}` — предварительная витрина
конкретной организации. Backend выбирает только точный публичный slug; неизвестный
slug получает 404. Frontend проверяет DTO и соответствие slug, не подставляет
другую организацию при ошибке. Сейчас это проверка публичных демо-данных,
**не доказательство изоляции приватных tenant-данных**.

## Контракт следующего этапа: авторизация и PostgreSQL RLS

Перед первой приватной бизнес-таблицей необходимо реализовать вместе:

1. Глобальный user как идентификатор входа; tenants, memberships и клиентские
   профили внутри tenant. Права определяются `(user_id, tenant_id, role, state)`.
2. Неизменяемый scope создаётся сервером после проверки сессии и активного
   membership. Ни slug, ни URL/body/header с tenant ID сами по себе не дают прав.
   Клиент дополнительно ограничен своим tenant-scoped профилем, курьер — заданиями.
3. Репозитории требуют scope как обязательный аргумент и явно фильтруют tenant ID.
   Публичные DTO доступны отдельно и не включают клиентские/служебные данные.
4. Миграционный владелец и runtime-роль БД раздельны. Runtime — `NOSUPERUSER`,
   `NOBYPASSRLS`, без владения таблицами и без DDL. Локальная foundation-роль
   Compose является владельцем для миграций и не подходит для приватных данных.
5. Для tenant-таблиц — `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY`,
   политика с `USING` и `WITH CHECK`, сравнивающими `tenant_id` с
   `nullif(current_setting('app.tenant_id', true), '')::uuid`. Без scope — deny.
6. Scope устанавливается параметризованным
   `SELECT set_config('app.tenant_id', :tenant_id, true)` **внутри каждой транзакции**.
   Запрещён session-level `SET`: пул не должен переносить контекст между tenants.
   RLS — второй уровень защиты от ошибок запросов, не замена API-авторизации.
7. Связи используют composite FK `(tenant_id, id)` и соответствующие unique keys.
   Фоновые задания/экспорты/файлы проходят те же границы; job получает tenant ID,
   повторно устанавливает scope и применяет idempotency/deduplication key.
8. Тесты на настоящем PostgreSQL под runtime-ролью: cross-tenant SELECT/INSERT/
   UPDATE/DELETE, отсутствие scope, composite FK, повторное использование pool,
   HTTP-подмена tenant ID, роли и клиентские права. Приватные маршруты не публиковать
   до прохождения этих тестов.

Эти правила зафиксированы как контракт; RLS и SMS-авторизация пока не реализованы.

## Health, миграции, worker

`/api/v1/health/live` сообщает только о живом API-процессе. `/api/v1/health/ready`
параллельно проверяет Redis PING и соединение с PostgreSQL с актуальным Alembic head.
Отказ/таймаут/неприменённые миграции дают 503; детали ошибок и credentials не выходят
в ответ. Web `/api/health/ready` проверяет готовность API; `/api/health/live`
независим от API. Ответы health не кешируются.

Compose запускает postgres → one-shot migrate → api → web; Redis требуется API.
Миграции выполняются отдельно, а не каждым API-worker при старте.
`app/jobs` обозначает будущий worker (arq): Redis реально работает, jobs пока нет.
Очередь вводится с первой реальной задачей, без fake-SMS и платёжных адаптеров.

## Будущие доменные ограничения

Деньги — integer minor units; UTC в БД, IANA timezone на уровне филиала.
Онлайн и телефонные заказы вызывают один booking service. Защита от наложений
приборов — PostgreSQL-транзакции и exclusion constraints, не только Redis lock.
Потоки SaaS-подписки и оплаты аренды разделены; клиент платит своему арендодателю.
Не вводить marketplace, реальные SMS, эквайринг, личные/медицинские данные на этапе 0.

## Ограничения

Compose предназначен для локальной разработки, порты привязаны к loopback.
Нет runtime DB-role, tenant auth, RLS, backup/restore, rate limits,
наблюдаемости, активного worker или полноценного offline PWA. Manifest — только
shell; service worker отсутствует, приватные ответы никогда не должны кешироваться
offline. Реальные данные и production запуск требуют отдельных этапов из ТЗ.

## Staging CD (добавление)

`compose.staging.yaml` использует отдельный project `photolamp-test`, private network,
RAID bind mount и существующий Traefik для HTTPS/Basic Auth. В API добавлен явный
режим staging: synthetic preview допустим только с отдельным DB password и Git SHA;
production по-прежнему запрещает demo. Приватные доменные данные не вводятся.
Web/API SHA запекается в образ; `/api/version` проверяет совпадение двух процессов.
CD допускает только успешный CI push на точной текущей main-ревизии, использует
проверенный SSH host key и host-side flock. Образы собираются на сервере; registry
и отдельный server→GitHub ключ для публичного репозитория не нужны.
DNS/SSH/Traefik/.env и enabling variable требуют однократной настройки владельцем;
само добавление workflow не означает, что домен уже опубликован. См. `../deployment.md`.

## Landing, этап 1

Главная `/` теперь ориентирована на семейный сценарий аренды, сохраняя отдельный
нижний B2B-блок и ссылки на две демо-витрины. Общий компонент `RentalLanding`
принимает имя, город и акцент, а `/r/{tenantSlug}` получает их из проверенного
публичного DTO. Клиентский планировщик срока/получения не отправляет данные,
не считает цену и не включает checkout. Приватные доменные данные, auth и RLS
этот UI-этап не вводит; их реализация остаётся обязательным следующим этапом.
