# ADR 0023: immutable off-site backup и внешний restore drill

Статус: принято. Дата: 2026-10-15.

## Контекст

Staging создаёт AES-256-GCM архив PostgreSQL на RAID и проверяет его реальным
восстановлением. Этот архив не защищает от потери сервера, RAID, учётной записи
хоста или локального keyring. Для реальных контактов нужна независимая копия,
которую скомпрометированный deploy-пользователь не сможет перезаписать или удалить
до окончания установленного срока.

## Решение

- После успешного локального restore drill host может передать уже зашифрованный
  архив отдельному краткоживущему `offsite-backup` maintenance-контейнеру.
- Контейнер получает только S3-compatible endpoint, ограниченную пару credentials
  и поток ciphertext. Он не получает database URL, контактные/backup encryption
  keys, volume PostgreSQL, локальный backup volume или внутреннюю Docker-сеть.
- Для контейнера создана отдельная outbound-only `backup-egress` сеть. Порты на
  host не публикуются; application containers к этой сети не подключаются.
- Объект создаётся один раз с `If-None-Match: *`, SHA-256 checksum/metadata и
  `COMPLIANCE Object Lock`. Отсутствующий lock, меньший срок, неверный checksum,
  размер или revision делают backup job неуспешным.
- После загрузки тот же объект скачивается обратно в временный mode-700 каталог,
  сверяется по SHA-256, расшифровывается только изолированным crypto-процессом и
  восстанавливается в одноразовую БД с проверкой Alembic/RLS. Временная копия и
  БД удаляются.
- Плановый monitor проверяет наличие, checksum metadata и оставшийся Object Lock
  последнего архива. Никаких list/delete операций приложению не требуется.
- Интеграция выключена по умолчанию. При `OFFSITE_BACKUP_ENABLED=false` логи явно
  говорят, что authoritative copy остаётся локальной. Наличие кода не обозначает
  внешний backup как работающий.

## Требования к bucket и credentials

Bucket создаётся отдельно с versioning и Object Lock, включённым при создании.
Lifecycle удаляет версии только после окончания compliance retention. Credentials
ограничиваются записью новых объектов, установкой retention и чтением/head
объекта/retention внутри одного prefix; права overwrite/delete, управление
lifecycle/policy/default retention и доступ к другим bucket не нужны.
Backup keyring хранится отдельно от bucket и сервера в защищённом хранилище.

## Ограничения

Репозиторий не создаёт account, bucket, IAM policy или DNS и не содержит реальные
credentials. Независимость от SelfOS подтверждается только после настройки
внешнего провайдера и успешного restore drill. Для production также нужны
периодическая ручная проверка восстановления из отдельной operator-среды, учёт
стоимости/квот и документированная процедура disaster recovery. Текущий transport
использует один S3 `PutObject` и отклоняет архивы больше 5 GiB; до приближения к
этому размеру потребуется checksum-защищённая multipart-загрузка.
