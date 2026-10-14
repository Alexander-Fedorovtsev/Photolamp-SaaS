# ADR 0022: Telegram on-call alerts для staging

Статус: принято. Дата: 2026-10-15.

## Контекст

GitHub Actions запускает staging monitor каждые 15 минут и уже обнаруживает
неверную ревизию, нездоровые контейнеры, проблемы HTTPS/метрик и устаревший либо
повреждённый backup. Красный run виден в GitHub, но не даёт отдельного срочного
сигнала ответственному человеку. Deploy и ежедневный backup также требуют такого
сигнала при отказе.

## Решение

- GitHub Environment `staging` хранит `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_ALERT_CHAT_ID` и необязательный `TELEGRAM_ALERT_THREAD_ID` как secrets.
  Токен не передаётся на staging-сервер и не хранится в `.env`.
- `Monitor staging`, `Deploy staging` и `Backup staging` вызывают общий helper
  только при `failure()`. В канал уходят среда, имя проверки, короткий Git SHA,
  UTC-время и ссылка на GitHub Actions. Логи, exception text, HTTP payload,
  tenant/customer ID и персональные данные в сообщение не включаются.
- Ручной запуск `Monitor staging` с input `send_test_alert=true` проверяет права
  бота и выбранный chat/topic без искусственного отказа staging.
- Helper проверяет формат всех входов, отправляет plain-text JSON через HTTPS,
  не выводит Telegram response и возвращает non-zero при неверной конфигурации,
  сетевой ошибке или отказе Bot API.
- Бот имеет только право отправки сообщений в отдельный приватный операционный
  chat/channel. Он не участвует в клиентских и tenant-уведомлениях и не принимает
  команды управления инфраструктурой.

## Ограничения

Это один alert transport, зависящий от GitHub Actions и Telegram. Повторный сбой
плановой проверки может отправлять повторное сообщение; автоматическая дедупликация,
acknowledgement, расписание дежурств и escalation policy пока отсутствуют. Telegram
не заменяет независимый uptime probe, централизованное хранение telemetry или
production incident management. Подключение считается рабочим только после
успешного ручного test alert в реальный on-call destination.
