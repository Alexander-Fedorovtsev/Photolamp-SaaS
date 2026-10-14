# Демо на photolamp-test.anliwei.pro и CD

Используется тот же Ubuntu/Docker/Traefik-сервер, что для SelfOS. Схема:
PR → CI → merge в main → успешный CI push run → GitHub Actions → SSH →
точный checkout → staging Compose → HTTPS/readiness/версия.

Это staging с искусственными данными. Basic Auth защищает весь сайт. Публичное
оформление, SMS и платежи отключены. В staff-панели можно создать заказ только на
фиксированный синтетический контакт. Production-режим API запрещает demo previews.
Notification jobs для выдачи, продлений и возвратов сохраняются как
`blocked/provider_disabled`; staging не запускает отправку и не показывает их как
доставленные.

## 1. DNS и существующий Traefik

В DNS зоны `anliwei.pro` добавьте:

| Тип | Имя | Значение |
| --- | --- | --- |
| A | `photolamp-test` | Публичный IPv4 того же сервера, куда направлен `self.anliwei.pro` |

Если используете AAAA, он должен указывать на работающий IPv6 этого же сервера.
На время первого выпуска сертификата при Cloudflare выберите DNS only.
Traefik уже должен слушать 80/443 и иметь Docker network `traefik` и ACME resolver
`myresolver`, как в SelfOS. Его не нужно переустанавливать или перезапускать:
Photolamp публикует собственные Docker labels. Если resolver называется иначе,
задайте его имя в `TRAEFIK_CERT_RESOLVER` файла staging `.env`.

## 2. Подготовить сервер один раз

Нужны Docker 28+, Compose 2.35+, git, curl, bash, python3, openssl, util-linux
(flock/mountpoint), apache2-utils (htpasswd), OpenSSH. `/mnt/raid` уже смонтирован.
Если этих пакетов нет, администратор устанавливает их отдельно; существующий
Docker/Traefik и SelfOS менять не требуется.

Frontend обращается к API по уникальному DNS alias `photolamp-test-backend`
в приватной сети Photolamp. Общее имя `api` использовать нельзя: в сети Traefik
оно может принадлежать API другого проекта и приводить к ответам 404.

Как администратор сервера:

```bash
sudo apt-get update
sudo apt-get install -y git curl python3 openssl util-linux apache2-utils
sudo adduser --disabled-password --gecos '' photolamp-deploy
sudo usermod -aG docker photolamp-deploy
docker network inspect traefik >/dev/null
git clone https://github.com/Alexander-Fedorovtsev/Photolamp-SaaS.git /tmp/photolamp-bootstrap
sudo bash /tmp/photolamp-bootstrap/infra/scripts/bootstrap-host.sh photolamp-deploy
```

Если пользователь или временная копия уже существуют, повторно их не создавайте.
Bootstrap создаёт только `/opt/photolamp-test`, `/mnt/raid/photolamp-test/postgres`
и закрытый каталог `/mnt/raid/photolamp-test/backups`.
PostgreSQL **Debian** в этом проекте имеет UID/GID **999:999**, не 70:70 из Alpine
SelfOS. Bootstrap не выполняет рекурсивный chown и не трогает данные SelfOS.

Откройте новую сессию deploy-пользователя, чтобы применилось членство в docker:

```bash
sudo -iu photolamp-deploy
docker info >/dev/null
git clone https://github.com/Alexander-Fedorovtsev/Photolamp-SaaS.git /opt/photolamp-test
cd /opt/photolamp-test
cp infra/staging.env.example .env
chmod 600 .env
openssl rand -hex 32
bash infra/scripts/generate-basic-auth.sh
nano .env
```

Заполните `.env`:

- `POSTGRES_PASSWORD`: результат `openssl rand -hex 32` (64 hex-символа).
- `TRAEFIK_BASIC_AUTH_USERS`: полная строка из helper, включая одинарные кавычки
  вокруг bcrypt. Сохраните `$` буквально; не заменяйте их на `$$`.
- `PHOTOLAMP_HEALTHCHECK_USER`: тот же username, который указали helper.
- `PHOTOLAMP_HEALTHCHECK_PASSWORD`: тот же пароль, в одинарных кавычках.
- `TRAEFIK_CERT_RESOLVER=myresolver` либо фактическое имя resolver SelfOS.

`CONTACT_ACTIVE_KEY_VERSION` и `CONTACT_ENCRYPTION_KEYS` первый deploy добавляет
атомарно. Существующий staging получает совместимую версию 1, чтобы не потерять
доступ к прежним синтетическим строкам; новая установка получает случайный ключ.
`OTP_PEPPER` пока можно не добавлять: staging принимает только фиксированный
синтетический контакт. До реальных контактов сохраните весь contact keyring
off-host и задайте уникальный `OTP_PEPPER`.

`BACKUP_ACTIVE_KEY_ID` и `BACKUP_ENCRYPTION_KEYS` вручную генерировать не нужно:
первый deploy атомарно добавит уникальный versioned keyring в `.env`, не печатая
его в лог. После успешного deploy сохраните обе полные строки в отдельном
off-host password manager. Не отправляйте keyring в чат, issue или CI secret:
он нужен именно для расшифровки архивов при потере сервера.

Файл принадлежит `photolamp-deploy`, mode 600. Реальные значения остаются только
на сервере, не в репозитории или чате. `PHOTOLAMP_DEPLOY_SHA` туда не записывайте:
CD передаёт его отдельно и запекает в оба образа. API/web/БД не получают пароль
healthcheck; web не получает credentials базы.

Репозиторий Photolamp публичный, поэтому сервер скачивает его по HTTPS без PAT
и дополнительного GitHub deploy key. Это единственное отличие от SSH alias
`github-selfos` для приватного SelfOS.

## 3. SSH ключ GitHub Actions → сервер

На своём администраторском компьютере создайте отдельную пару ключей:

```bash
ssh-keygen -t ed25519 -f photolamp_actions -C photolamp-staging-cd -N ''
```

На сервере добавьте содержимое `photolamp_actions.pub` в
`/home/photolamp-deploy/.ssh/authorized_keys` (mode каталога 700, файла 600,
владелец photolamp-deploy). Можно добавить `restrict ` перед `ssh-ed25519`:
CD использует только неинтерактивный SSH exec, без PTY и forwarding.
Существующие ключи не заменяйте. Закрытый файл `photolamp_actions` нужен только
для GitHub secret, не для server→GitHub.

Проверьте с администраторского компьютера:

```bash
ssh -i photolamp_actions -p 22 photolamp-deploy@SERVER_IP 'docker info >/dev/null'
```

Замените `SERVER_IP` и порт на значения, которые используете для SelfOS.

Для pinning host key получите **публичный** ключ через доверенную консоль сервера:

```bash
sudo awk '{print $1, $2}' /etc/ssh/ssh_host_ed25519_key.pub
```

В `STAGING_KNOWN_HOSTS` сохраните одну строку:

```text
SERVER_IP ssh-ed25519 PUBLIC_HOST_KEY_FROM_SERVER
```

При нестандартном порте формат: `[SERVER_IP]:PORT ssh-ed25519 PUBLIC_HOST_KEY_FROM_SERVER`.
Имя/IP должно совпадать с `STAGING_HOST`. Это публичный host key сервера,
не `photolamp_actions.pub`. Workflow не делает слепой ssh-keyscan и требует
`StrictHostKeyChecking=yes`.

## 4. GitHub Environment, Secrets и Variable

В репозитории **Photolamp-SaaS**, Settings → Environments, создайте `staging`.
Deployment branches: selected branches → `main`. Для полностью автоматического
CD не добавляйте required reviewers к этому environment.

Remote deploy делает до пяти попыток `git fetch` с ограниченной задержкой. Это
переживает краткие сетевые разрывы между staging-сервером и GitHub; после
исчерпания попыток job завершается без checkout и без переключения контейнеров.
Проверка точного успешного CI run также повторяет запрос к GitHub API до шести
раз: событие `workflow_run` может прийти раньше, чем этот run появится в
отфильтрованном Actions API. SHA, ветка, тип события, репозиторий и итоговый
`success` проверяются на каждой попытке; другой или неуспешный run не допускается.
SSH-клиент делает встроенные connection attempts и до четырёх запусков при коде
транспортной ошибки 255. Ошибка удалённого deploy-скрипта не повторяется: это
сохраняет исходную диагностику сборки, миграции или health-check.

В environment `staging` → Secrets добавьте:

| Secret | Значение |
| --- | --- |
| `STAGING_HOST` | SSH IP/hostname того же сервера; без `https://` |
| `STAGING_USER` | `photolamp-deploy` |
| `STAGING_SSH_PORT` | SSH-порт сервера, например `22` |
| `STAGING_SSH_KEY` | Полное содержимое закрытого `photolamp_actions` |
| `STAGING_KNOWN_HOSTS` | Проверенная строка host key из предыдущего шага |

Settings → Secrets and variables → Actions → **Variables**:

```text
STAGING_DEPLOY_ENABLED=false
```

После подготовки DNS, `.env`, SSH и Docker переключите эту **repository variable**
на `true`. Пароли Basic Auth и Postgres в GitHub добавлять не нужно.
CD не запускается при отсутствующей variable или значении, отличном от `true`.

## 5. Первый запуск и дальнейший CD

После успешного CI на `main` откройте Actions → **Deploy staging** → Run workflow →
branch `main`. Ручной запуск тоже проверит успешный CI push run точного SHA.
Затем каждый merge в `main` автоматически запускает CI и, после успеха, CD.
PR, feature-ветки и неуспешные CI не деплоятся. При устаревшем CI SHA deploy
пропускается, чтобы не откатить уже продвинувшийся `main`.

Workflow подключается по SSH, проверяет origin и чистоту tracked-файлов,
берёт flock до checkout/build, делает detached checkout точного `origin/main`,
запускает `infra/scripts/deploy.sh`. `.env` и данные не заменяются.

При первом деплое tenant foundation скрипт атомарно добавляет в защищённый `.env`
отдельный 64-символьный `POSTGRES_RUNTIME_PASSWORD`, если строка отсутствует или
содержит документированный placeholder. Значение не печатается. Затем bootstrap
создаёт/обновляет `photolamp_runtime`; owner credentials получает только migration
контейнер, API работает под runtime-ролью без DDL и `BYPASSRLS`.

Миграция телефонных заказов создаёт только forward-compatible таблицы и поля.
CD не печатает ключи. Контактные ciphertext доступны runtime-роли только внутри
tenant RLS-транзакции; staff-очередь не возвращает телефон, имя или адрес.

Деплой проверяет конфигурацию, отсутствие published ports, isolated network,
bcrypt и credentials, собирает образы с SHA, применяет forward migration,
проверяет покрытие всех contact ciphertext версиями keyring и ожидает готовность.
Снаружи проверяются сертификат HTTPS, Basic Auth, readiness и совпадение SHA у
**web и API**. Ошибка делает job красным; автоматический database
downgrade/rollback отсутствует. Обновление может дать короткий перерыв.

Web-контейнер получает явный `APP_ENV=staging`: staff cookies поэтому всегда
`Secure`, независимо от наличия proxy header. Next.js выставляет CSP/HSTS и
другие browser security headers, а изменяющие `/api/admin/*` запросы принимает
только при совпадающем `Origin`. Это не включает SMS: provider остаётся disabled.

Откройте:

- <https://photolamp-test.anliwei.pro>
- <https://photolamp-test.anliwei.pro/r/demo-ufa>
- <https://photolamp-test.anliwei.pro/r/demo-kazan>

Браузер попросит username/password, заданные helper.
Проверка версии без пароля в истории команд (curl спросит его интерактивно):

```bash
curl --user photolamp https://photolamp-test.anliwei.pro/api/health/ready
curl --user photolamp https://photolamp-test.anliwei.pro/api/version
```

Если username другой, замените `photolamp`. Ответы: `status=ready` и одинаковые
`git_sha`/`api_git_sha`, совпадающие с SHA успешного CD.
Номер логина Basic Auth не является телефоном или tenant-авторизацией клиента.

## Диагностика и отключение CD

Если deploy остановился на `Invalid staging Compose configuration`, следующая часть
сообщения теперь указывает конкретное нарушенное условие, без вывода значений `.env`.
Например, `64-character random hex database password` требует 64 hex-символа,
а `Basic Auth must contain one intact bcrypt hash` — полную строку helper с
одинарными кавычками и неизменёнными `$`. Исправьте только указанное поле в серверном
`.env` и повторите deploy; файл с паролями в чат или логи копировать не требуется.

Как deploy-пользователь в `/opt/photolamp-test`:

```bash
export PHOTOLAMP_DEPLOY_SHA="$(git rev-parse HEAD)"
docker compose -p photolamp-test --env-file .env -f compose.yaml -f compose.staging.yaml ps
docker compose -p photolamp-test --env-file .env -f compose.yaml -f compose.staging.yaml logs --tail 80 api web db-bootstrap migrate
```

CD не использует `down --volumes`, Docker prune, git clean, замену `.env` или
перезапуск Traefik. Отключение новых деплоев: `STAGING_DEPLOY_ENABLED=false`.
Работающий сайт при этом продолжает обслуживаться.

## Ротация ключей контактных данных

Команда генерирует новый ключ, атомарно дополняет keyring, делает новую версию
активной, перезапускает только API и пакетно перешифровывает приватные профили и
детали заказов. Старые ключи сохраняются; plaintext не выводится и не записывается
на диск. Запускайте от `photolamp-deploy` из актуального checkout:

```bash
cd /opt/photolamp-test
bash infra/scripts/rotate-contact-key.sh 2
```

Если процесс прервался после активации ключа, API продолжает читать старые и новые
строки и пишет только новой версией. Не создавайте ещё одну версию: завершите
существующую напрямую, указав активную версию:

```bash
export PHOTOLAMP_DEPLOY_SHA="$(git rev-parse HEAD)"
docker compose -p photolamp-test --env-file .env \
  -f compose.yaml -f compose.staging.yaml --profile maintenance run --rm --no-deps \
  contact-maintenance --target-version 2
```

Сначала сохраните обновлённые строки `CONTACT_ACTIVE_KEY_VERSION` и
`CONTACT_ENCRYPTION_KEYS` в защищённом off-host password manager. После успешной
ротации старый ключ можно удалить только helper-командой: она проверяет обе
приватные таблицы и откажется, если версия ещё используется.

```bash
bash infra/scripts/retire-contact-key.sh 1
```

Не редактируйте keyring вручную и не публикуйте его в терминальных логах, GitHub,
чатах или issue. История запусков в `contact_key_rotation_runs` содержит только
версии, статусы и счётчики строк.

## Резервные копии и restore drill

После каждого deploy и ежедневно в 01:17 UTC workflow **Backup staging** делает
PostgreSQL custom dump. Поток шифруется AES-256-GCM до записи на диск; plaintext
файл не создаётся. Архив считается готовым только после checksum, authenticated
decrypt и настоящего `pg_restore` в одноразовую БД. Проверяются Alembic, основные
таблицы и forced RLS; тестовая БД затем удаляется. По умолчанию сохраняются файлы
не старше 30 дней и не более 35 архивов.

Ручной запуск от `photolamp-deploy`:

```bash
cd /opt/photolamp-test
bash infra/scripts/backup-database.sh
ls -l /mnt/raid/photolamp-test/backups
```

Проверить существующий архив без изменения основной БД:

```bash
bash infra/scripts/verify-backup.sh \
  /mnt/raid/photolamp-test/backups/photolamp-YYYYMMDDTHHMMSSZ-v1-XXXXXX.dump.plb
```

Для ротации создайте новый ID; helper сам генерирует ключ, сохраняет mode 600,
делает новый ID активным и оставляет прежние ключи для старых архивов:

```bash
python3 infra/scripts/rotate-backup-key.py .env v2
bash infra/scripts/backup-database.sh
```

После успешного drill обновите off-host копию всего keyring. Старый ключ удаляйте
из `.env` только когда архивов с его ID больше нет и независимая копия keyring
сохранена. Имя файла содержит ID (`-v1-`, `-v2-`), но не сам ключ.

Эти backup находятся на том же RAID. До работы с реальными контактами нужен
отдельный object storage с отдельными credentials, versioning/immutability и
проверка восстановления именно из внешней копии. Сейчас такая интеграция не
настроена и не обозначается как работающая.

## Автоматические проверки схемы

```bash
python3 -m unittest discover -s tests/infra -v
shellcheck infra/scripts/*.sh tests/infra/test-edge.sh
bash tests/infra/test-edge.sh
```

Последняя команда — только для одноразового CI/dev-окружения, **не для сервера SelfOS**.
Она создаёт временные PostgreSQL/Redis/API/web и Traefik, выдаёт локальный HTTPS
сертификат, проверяет TLS с trusted CA (без insecure), 401/200 для Basic Auth,
целостность bcrypt в реальной Docker label, витрины и SHA. После теста удаляются
только ресурсы этого временного project. В managed Codex используйте
`PHOTOLAMP_CLOUD_BUILD=true bash tests/infra/test-edge.sh` для CA build secrets.
