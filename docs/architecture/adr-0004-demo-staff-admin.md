# ADR 0004: demo staff session, tenant catalog CRUD и аудит

Статус: принято. Дата: 2026-10-13.

## Контекст

Каталог уже хранится под PostgreSQL RLS, но управлять им можно было только через
миграции. Для проверки рабочего процесса арендодателя нужна usable панель, при
этом реальные SMS, телефоны сотрудников и production-аутентификация ещё не
подключены. Нельзя выдавать demo-механизм за готовый вход для реального бизнеса.

## Решение

- `POST /api/v1/auth/demo-session` работает только при включённом demo-режиме и
  всегда возвращает `404` в production. Он создаёт случайный opaque token со
  сроком четыре часа; PostgreSQL получает только SHA-256 hash.
- Узкая `SECURITY DEFINER` функция может выпустить сессию только фиксированному
  синтетическому пользователю, который состоит исключительно в двух опубликованных
  demo tenant. Функция ограничивает максимальный срок восемью часами.
- Next.js принимает token по внутренней сети, сохраняет его в `HttpOnly`,
  `SameSite=Strict` cookie и проксирует staff-запросы. Bearer token не доступен
  клиентскому JavaScript. На HTTPS cookie получает `Secure`.
- `/admin` показывает только memberships текущей сессии. Выбранный tenant снова
  авторизуется API через `authorize_session_tenant`; ID из URL или тела сам по
  себе не даёт доступ.
- `GET /api/v1/t/{tenant_id}/catalog-admin` возвращает приватную staff read-model.
  `POST/PATCH` для `branches`, `models`, `assets`, `pricing` доступны ролям
  `owner/admin`. `tenant_id` всегда берётся из неизменяемого server context и
  отсутствует в command DTO.
- `audit_events` хранит actor, действие, сущность и изменённые поля. Таблица также
  использует `ENABLE/FORCE RLS`; события пишутся в той же транзакции, что и
  изменение каталога.
- Составные tenant FK из ADR 0003 остаются последней защитой от связи asset или
  pricing с данными другой организации.

## Границы

Demo-вход предназначен только для синтетических local/staging данных и не заменяет
OTP, 2FA, rate limiting, управление сотрудниками или recovery. Заказы, реальные
клиенты и платежи отсутствуют. Production остаётся без этого endpoint.

## Проверки и следующий шаг

Integration-тест проходит полный путь session → membership → CRUD → public
read-model → audit → logout под настоящей runtime-ролью. Smoke проверяет admin
через собранные контейнеры, Playwright — tenant switch, отсутствие строк другого
tenant, keyboard navigation, axe и mobile/desktop layouts.

Следующий бизнес-инкремент — серверные availability и quote на диапазон дат. До
атомарного hold и создания заказа кнопки оформления остаются отключёнными. Реальный
staff login должен быть отдельным auth-инкрементом до подключения первого клиента.
