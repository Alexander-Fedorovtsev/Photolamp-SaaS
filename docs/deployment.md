# Демо на photolamp-test.anliwei.pro и CD

Используется тот же Ubuntu/Docker/Traefik-сервер, что для SelfOS. Схема:
PR → CI → merge в main → успешный CI push run → GitHub Actions → SSH →
точный checkout → staging Compose → HTTPS/readiness/версия.

Это staging с искусственными данными. Basic Auth защищает весь сайт. Заказы,
SMS и платежи отключены. Production-режим API по-прежнему запрещает demo previews.

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
Bootstrap создаёт только `/opt/photolamp-test` и `/mnt/raid/photolamp-test/postgres`.
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

Деплой проверяет конфигурацию, отсутствие published ports, isolated network,
bcrypt и credentials, собирает образы с SHA, применяет forward migration и
ожидает готовность. Снаружи проверяются сертификат HTTPS, Basic Auth, readiness
и совпадение SHA у **web и API**. Ошибка делает job красным; автоматический
database downgrade/rollback отсутствует. Обновление может дать короткий перерыв.

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

Как deploy-пользователь в `/opt/photolamp-test`:

```bash
export PHOTOLAMP_DEPLOY_SHA="$(git rev-parse HEAD)"
docker compose -p photolamp-test --env-file .env -f compose.yaml -f compose.staging.yaml ps
docker compose -p photolamp-test --env-file .env -f compose.yaml -f compose.staging.yaml logs --tail 80 api web migrate
```

CD не использует `down --volumes`, Docker prune, git clean, замену `.env` или
перезапуск Traefik. Отключение новых деплоев: `STAGING_DEPLOY_ENABLED=false`.
Работающий сайт при этом продолжает обслуживаться.

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
