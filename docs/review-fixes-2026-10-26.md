# Исправления по ревью — 26 октября 2026

Дополнение от 28 октября: [повторная проверка перед production](production-readiness-2026-10-28.md) выявила дополнительные условия сохранения runtime и проверки Xray. Первоначальные тесты не являются разрешением на применение к неизвестному серверу.

Изменения подготовлены в рабочем дереве проекта. На production они не применялись. Практические проверки выполнялись только в изолированных Bubblewrap-контейнерах; пакеты и сервисы основной системы не изменялись.

## Версии

| Компонент | Закреплённая версия |
|---|---|
| 3x-ui | 3.11.0 + OCI index SHA-256 |
| ansible-core | 2.21.5 |
| ansible-lint | 26.10.1 |
| pre-commit | 4.7.0 |
| yamllint | 1.38.0 |
| ShellCheck wheel / binary | 0.11.0.1 / 0.11.0 |
| gitleaks | 8.30.1 |
| actionlint | 1.7.12 |
| Molecule | 26.10.0 |
| molecule-plugins | 26.10.12 |
| Docker SDK for Python | 7.2.0 |
| community.docker / ansible.posix | 5.4.0 / 2.2.2 |
| actions/checkout | 7.0.1, full commit SHA |
| actions/setup-python | 7.1.0, full commit SHA |
| actions/cache | 6.1.0, full commit SHA |
| gitleaks-action | 3.0.0, full commit SHA |

Образ 3x-ui: `ghcr.io/mhsanaei/3x-ui:v3.11.0@sha256:1628adbd9bed680d25d6f714bef4a20f132105f4612ea294952f6665dad32e5b`.

Версии сверены с официальными release/tag API и PyPI/Galaxy на дату проверки. Источники: [3x-ui release](https://github.com/MHSanaei/3x-ui/releases/tag/v3.11.0), [ansible-core](https://pypi.org/project/ansible-core/2.21.5/), [ansible-lint](https://pypi.org/project/ansible-lint/26.10.1/), [Molecule](https://pypi.org/project/molecule/26.10.0/), [gitleaks](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1).

Docker Engine и ОС рабочего компьютера не обновлялись. На целевом сервере их обновление оставлено отдельной операцией после инвентаризации других сервисов; обычное обновление Vipen не запускает общий apt upgrade.

## Связь с замечаниями аудита

| Замечание | Что изменено | Ограничение/проверка |
|---|---|---|
| F01 — устаревший 3x-ui | 3.11.0, неизменяемый digest | Реальные бинарники и synthetic migration; production-копию ещё нужно проверить |
| F02 — потеря доступа при смене SSH-порта | Общий playbook отклоняет смену; hardening не трогает listener | Отдельная двухэтапная процедура в migration.md |
| F03 — сброс firewall | Reset удалён; управляются только hash-labelled правила; watchdog 180 секунд | Реальный сетевой rollback требует отдельной VM |
| F04 — UPDATE нескольких REALITY-входов | Выбор ровно одного id/tag; неоднозначный порт отклоняется; транзакция | SQLite regression и реальная миграция двух входов |
| F05 — перезапись REALITY/default secrets | Пустые overrides; обязательный настоящий secrets.yml | Тест сохранения настроек и preflight |
| F06 — поздняя проверка restore и отсутствие отката | Schema/integrity и pull до stop; isolated trial; staged DB; возврат DB/image pair | Fault tests; реальный Docker Compose ещё не прогонялся |
| F07 — потеря чужих daemon.json параметров | Merge только live-restore, validate и backup | Применение Docker daemon требует системной интеграции |
| F08 — секреты в argv/ошибках | Конфигурация через stdin, no_log и очищенный результат helper | Тест утечки в failure output и Docker argv |
| F09 — HTTP subscription | TLS обязателен; fresh install выключает все виды подписок | Сертификаты/продление на сервере настраивает оператор |
| F10 — python3-debian | Добавлен до deb822_repository | Проверено lint; реальная установка пакетов в VM впереди |
| F11 — фиктивный authorized_keys | ssh-keygen и отдельный key-only SSH-вход | Реальный ssh-keygen отклоняет файл комментариев |
| F12 — SSH precedence/Match | Managed include перед global settings; sshd -t/-T -C, recovery | Настоящий sshd проверен на синтетических файлах, не live SSH |
| F13 — false FAIL пустой установки | Listeners из DB; bootstrap WARN; строгий флаг после настройки | Regression + первый запуск 3.11.0 |
| F14 — ротация логов существующего контейнера | Logging в Compose, пересоздание сервиса при изменениях | Render regression; Docker runtime впереди |
| F15 — зависшие команды | Deadlines Docker/systemd/SQLite/backup и service timeouts | Тест зависшего Docker и deadline snapshot |
| F16 — выключенные флаги оставляли workers | Stop/disable scheduler и worker для health/host/digest/backup | Статическая проверка задач; фактический systemd впереди |
| F17 — пустой журнал считался успехом | Quiet output, проверка exit code и свежих плановых samples | Реальные Bash regression для пустого/ошибочного journal |
| F18 — неполная стратегия backup | БД + Compose + cert + image manifest, off-host fetch перед удалённым обновлением | Ежедневный внешний транспорт и restore drill нужны отдельно |
| F19 — старые правила и CIDR | Собственная ownership, добавление перед удалением, CIDR validation и проверка текущего адреса | Legacy rules требуют сверки оператором; IPv6 end-to-end впереди |
| F20 — Linux ControlPath | Удалён macOS-specific каталог | Синтаксис/конфигурация; новые SSH-проверки отключают multiplexing |
| F21 — общий upgrade по умолчанию | Выключен; выделен в обслуживание ОС | Никакого apt upgrade на основной системе |
| F22 — BBR/conntrack | Load до assert, kmod/iproute2, запрет случайного уменьшения conntrack, tuning opt-in | Реальная проверка ядра — на целевой тестовой VM |
| F23 — фиксированный subscription-порт | Действительное состояние/порт из DB в firewall и monitor | Regression нестандартного порта и выключения |
| F24 — пробелы тестов/опасная лаборатория | Регрессии критических путей, реальный binary smoke, ручной Molecule только на disposable runner | Полная системная матрица и внешний клиентский тест остаются gate для production |

Дополнительно: уменьшены capabilities контейнера, диагностический report защищён 0600, digest различает отсутствие данных и успешный день, подтверждённые доставки дедуплицируются, добавлены SECURITY/CONTRIBUTING/CHANGELOG и issue/PR templates. Actions закреплены SHA, Dependabot покрывает Actions и Python. SQLite sidecars и recovery exports исключены из Git.

## Проверки

- **52 регрессионных теста прошли**: Python/SQLite, deployment rollback, firewall/watchdog, архивы, отрендеренные Bash и Ansible payload/Compose.
- **Пройдены** Ansible syntax-check, ansible-lint production profile (0 ошибок, 0 предупреждений), yamllint, ShellCheck, actionlint, Black и git diff --check; локальные ссылки Markdown существуют.
- gitleaks 8.30.1: утечек в текущих исходниках и 43 просканированных коммитах истории не найдено.
- Реальный 3x-ui 3.11.0: создание администратора/схемы, настройка loopback, HTTP-ответ и согласованный snapshot работающей БД.
- Реальный 3.3.1 → 3.11.0: миграция synthetic DB, сохранение отдельных REALITY-ключей при одинаковом порте и открытие восстановленной старой БД прежним бинарником.
- Настоящие sshd/ssh-keygen: приоритет hardening, сохранение порта 2222, отсутствие дублирования port directive, обнаружение Match override и отказ на comment-only key file.

Лаборатория не подключалась к production и не использовала его БД. Docker daemon/systemd/UFW end-to-end, ARM64, вся матрица ОС, реальная отправка Telegram и внешний VPN-handshake не проверялись. Нельзя утверждать готовность конкретного production-сервера только по этим результатам.

## Что сделать перед production

Сначала read-only осмотр сервера по [списку необходимых данных](migration.md#данные-для-предстоящего-production-развёртывания). Затем полная репетиция на его отдельной копии, внешний backup с проверкой восстановления и конкретный план окна обслуживания/возврата. Реальные credentials и доступ к серверу в этой работе не требовались.
