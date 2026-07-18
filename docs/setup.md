# Vipen — детальная документация

Минимальный playbook для выделенного VPS с 3x-ui: базовая защита сервера, Docker, обновления системы, backup БД и запуск/обновление контейнера 3x-ui.

Playbook подходит для двух сценариев:

- чистый VPS: будет установлен Docker, поднят 3x-ui и закрыта панель от прямого доступа из интернета;
- существующий VPS: сохранится `/opt/vipen/db/x-ui.db`, вместе с клиентами, inbound-настройками и настройками панели.

Важно: playbook не создает VPN-клиентов и VLESS/REALITY inbound с нуля. На новом сервере их нужно создать в панели 3x-ui или восстановить существующую БД `x-ui.db`.

## Файлы

- `site.yml` - основной playbook.
- `inventories/production/hosts.example.yml` - шаблон инвентаря для запуска по SSH (скопируйте в `hosts.yml`).
- `inventories/production/hosts.yml` - ваш сервер, SSH-пользователь и ключ (в `.gitignore`).
- `inventories/production/hosts.local.example.yml` - шаблон инвентаря для запуска на самом сервере (скопируйте в `hosts.local.yml`).
- `inventories/production/hosts.local.yml` - локальный запуск, `ansible_connection: local` (в `.gitignore`).
- `inventories/production/group_vars/vpn_servers.yml` - центральный файл конфигурации (без секретов).
- `inventories/production/secrets.example.yml` - шаблон секретов с плейсхолдерами (коммитится).
- `inventories/production/secrets.yml` - ваши секреты: порт и путь панели, токен Telegram-бота. Зашифрован `ansible-vault` и в `.gitignore`.
- `roles/base` - базовые пакеты: `openssh-server`, `ufw`, `fail2ban`, `python3-apt`, `logrotate`.
- `roles/log_retention` - лимиты journald, чтобы системные логи не съедали диск.
- `roles/network_tuning` - sysctl-настройки TCP для VPN.
- `roles/backup` - backup БД 3x-ui: off-host копия на управляющую машину при удалённом запуске, on-host snapshot при локальном.
- `roles/ssh` - key-only SSH.
- `roles/firewall` - UFW: открыть VPN-порты и закрыть панель.
- `roles/docker` - Docker CE, compose plugin и Docker log rotation.
- `roles/system_updates` - безопасное обновление apt-пакетов.
- `roles/xui` - compose для 3x-ui, backup БД/compose и обновление контейнера.
- `roles/vpn_monitor` - легкий health-check через systemd timer.
- `roles/healthcheck` - ручная диагностическая проверка, которая падает при найденной проблеме.

## Перед первым запуском

Создайте инвентарь из примера (реальный `hosts.yml` в `.gitignore`, поэтому ваш IP и ключ не попадут в репозиторий). Все команды запускаются из корня репозитория:

```bash
cp inventories/production/hosts.example.yml \
   inventories/production/hosts.yml
```

Впишите свои значения в `hosts.yml`:

```yaml
ansible_host: <your-server-ip>
ansible_user: root
ansible_ssh_private_key_file: ~/.ssh/vipen
```

Создайте файл секретов (он в `.gitignore`, поэтому порт и путь панели, а также токен бота не попадут в репозиторий):

```bash
cp inventories/production/secrets.example.yml \
   inventories/production/secrets.yml
```

Как минимум задайте в нём `panel_port` — порт, на котором панель слушает на loopback. В примере стоит случайное значение, замените его своим. `xui_panel_path` оставьте пустым: тогда текущий путь панели сохранится из базы и его не придётся нигде записывать.

Затем зашифруйте файл:

```bash
ansible-vault encrypt inventories/production/secrets.yml
```

После шифрования playbook запускается с `--ask-vault-pass`. Подробности, включая настройку Telegram-оповещений, — в [`docs/alerting.md`](alerting.md).

Если `secrets.yml` не создать, playbook возьмёт значения из `secrets.example.yml` — он останется рабочим, но со случайным портом панели и без оповещений.

Проверьте `inventories/production/group_vars/vpn_servers.yml`:

```yaml
ssh_port: 22
ssh_admin_cidrs:
  - 0.0.0.0/0

vpn_tcp_ports: [443, 8443, 2096]
panel_port: <ваш-порт>          # задаётся в secrets.yml, не здесь
panel_public: false
xui_panel_listen: 127.0.0.1

xui_image: ghcr.io/mhsanaei/3x-ui:v3.3.1
xui_restore_db_enabled: false
xui_restore_db_local_path: ""
xui_restore_db_force: false
xui_subscription_enabled: true
xui_subscription_path: ""
xui_subscription_uri: ""
xui_subscription_encrypt: true
xui_subscription_show_info: false
xui_reality_overrides:
  - port: 443
    target: www.google.com:443
    server_names:
      - www.google.com
    fingerprint: randomized
    sniffing_enabled: true
    sniffing_dest_override:
      - http
      - tls
  - port: 8443
    target: www.icloud.com:443
    server_names:
      - www.icloud.com
    fingerprint: chrome
    sniffing_enabled: true
    sniffing_dest_override:
      - http
      - tls
system_updates_enabled: true

log_retention_enabled: true
log_retention_journald_system_max_use: 200M
log_retention_journald_system_keep_free: 1G
log_retention_journald_system_max_file_size: 50M
log_retention_journald_runtime_max_use: 50M
log_retention_btmp_logrotate_enabled: true
log_retention_btmp_logrotate_size: 25M
log_retention_btmp_logrotate_rotate: 1

network_tuning_sysctl_tuning_enabled: true
network_tuning_sysctl_settings:
  net.core.default_qdisc: fq
  net.ipv4.tcp_congestion_control: bbr
  net.netfilter.nf_conntrack_max: "65536"
network_tuning_apply_qdisc_to_default_route: true

vpn_monitor_enabled: true
vpn_monitor_interval: 1min
vpn_monitor_auto_restart: false
vpn_monitor_failure_threshold: 3

healthcheck_enabled: true
healthcheck_since: 30 minutes ago
healthcheck_docker_since: 30m
```

Что обычно меняется при переносе на другой сервер:

- `ansible_host` - IP нового VPS.
- `ansible_ssh_private_key_file` - ключ доступа.
- `ssh_admin_cidrs` - можно оставить `0.0.0.0/0` для доступа с телефона/роуминга, но безопаснее ограничить своими IP.
- `vpn_tcp_ports` и `vpn_udp_ports` - публичные VPN-порты.
- `xui_image` - версия 3x-ui для установки или обновления.
- `log_retention_journald_system_max_use` - максимальный размер persistent journal.
- `log_retention_btmp_logrotate_size` - размер, после которого ротируется журнал неудачных входов.
- `network_tuning_sysctl_settings` - kernel TCP tuning, по умолчанию `fq` и `bbr`.
- `vpn_monitor_auto_restart` - включать ли автоматический restart контейнера после нескольких failed health-check.

Секреты не держите в обычном `group_vars`: `x-ui.db`, client UUID, REALITY keys, shortIds, subscription path/URI. Для полного переноса сервера используйте restore БД из backup.

## Проверка

```bash
ansible-playbook site.yml --syntax-check
ansible-playbook site.yml --check --diff
```

Важно про `--check`: это не полное доказательство безопасности. Многие действия в ролях `firewall`, `docker`, `xui` — это `command`-задачи (ufw reset/enable, установка Docker, `docker compose pull/up`, записи в БД), которые в check-режиме пропускаются и в diff не видны. Diff показывают только шаблоны/файлы конфигов; кандидат `docker-compose.yml` валидируется отдельно через временный render. Для рискованных изменений проверяйте на staging-хосте или применяйте по `--tags`.

## Применение

```bash
ansible-playbook site.yml
```

## Локальный запуск (на самом сервере)

Тот же playbook можно запускать прямо на VPS, без SSH с другой машины. Для этого есть отдельный инвентарь `hosts.local.yml` с `ansible_connection: local`. Дефолтный `hosts.yml` при этом не меняется, удалённый сценарий работает как раньше.

`ansible.cfg` править не нужно: в нём по умолчанию прописан удалённый `hosts.yml`, а флаг `-i inventories/production/hosts.local.yml` переопределяет инвентарь на время конкретного запуска. Так удалённый режим остаётся поведением по умолчанию, а локальный включается только явным флагом.

Подготовка (один раз, на сервере с установленными `ansible-core` и `python3`, из корня репозитория):

```bash
cp inventories/production/hosts.local.example.yml \
   inventories/production/hosts.local.yml
```

Запуск от root на сервере:

```bash
ansible-playbook -i inventories/production/hosts.local.yml site.yml --syntax-check
ansible-playbook -i inventories/production/hosts.local.yml site.yml --check --diff
ansible-playbook -i inventories/production/hosts.local.yml site.yml
```

Отличие от удалённого запуска ровно одно — роль `backup`. При `ansible_connection: local` control node и есть сервер, поэтому off-host копия невозможна: вместо скачивания на отдельную машину playbook делает консистентный on-host snapshot БД в `/opt/vipen/backups/x-ui.db.snapshot-<timestamp>` и печатает напоминание, что это копия на том же диске. Для настоящей защиты периодически копируйте её на другую машину. Все остальные роли (`ssh`, `firewall`, `xui`, `docker`, `network_tuning`, monitoring и т.д.) работают идентично.

Важно про SSH-ключ. Роль `ssh` выключает парольный вход (`PasswordAuthentication no`). При удалённом запуске ключ уже есть — им подключился Ansible. При локальном запуске такой гарантии нет, поэтому положите публичный ключ своего компьютера в `ssh_authorized_keys` (в `hosts.local.yml` или `group_vars`). Роль поставит его в `/root/.ssh/authorized_keys` до выключения пароля. Если у root не окажется ни одного ключа, роль остановится с подсказкой и пароль не тронет (`ssh_require_authorized_key: true`). Приватный ключ на сервер класть не нужно — он живёт только на вашем компьютере. Заметьте: даже после выключения пароля вход через KVM/веб-консоль хостера по паролю продолжает работать (это не SSH), блокируется только удалённый SSH.

```yaml
# hosts.local.yml
ssh_authorized_keys:
  - "ssh-ed25519 AAAA...your-public-key... you@laptop"
```

## Частичный запуск

```bash
ansible-playbook site.yml --tags base
ansible-playbook site.yml --tags logs
ansible-playbook site.yml --tags network_tuning
ansible-playbook site.yml --tags ssh
ansible-playbook site.yml --tags firewall
ansible-playbook site.yml --tags docker
ansible-playbook site.yml --tags updates
ansible-playbook site.yml --tags xui
ansible-playbook site.yml --tags monitor
ansible-playbook site.yml --tags healthcheck
```

## Что делает по умолчанию

### SSH

- Оставляет SSH на порту `22`.
- Ставит публичные ключи из `ssh_authorized_keys` в `/root/.ssh/authorized_keys` до выключения пароля (не затирая уже существующие).
- Перед выключением пароля проверяет, что у root есть хотя бы один ключ; если нет — останавливается с подсказкой (`ssh_require_authorized_key: true`), чтобы не потерять удалённый доступ.
- Оставляет root-доступ по ключу, чтобы не потерять текущий способ входа.
- Выключает парольный вход: `PasswordAuthentication no`.
- Выключает keyboard-interactive auth: `KbdInteractiveAuthentication no`.
- Ограничивает root-login режимом `PermitRootLogin prohibit-password`.
- Снижает число попыток входа: `MaxAuthTries 3`.
- Проверяет конфигурацию через `sshd -t` перед reload.

Дальнейшее усиление: создать отдельного sudo-пользователя, проверить вход под ним и затем заменить root-доступ на `PermitRootLogin no`.

### Firewall

- Включает UFW.
- Ставит политику входящих соединений в `deny`.
- Оставляет исходящие соединения разрешенными.
- Открывает только заданные публичные порты:
  - `22/tcp` - SSH.
  - `443/tcp` - основной VLESS/REALITY.
  - `8443/tcp` - второй VLESS/REALITY inbound.
  - `2096/tcp` - subscription endpoint 3x-ui.
- Закрывает порт панели `panel_port/tcp`, если `panel_public: false`.

По умолчанию `firewall_reset: true`, поэтому playbook считает firewall своей зоной ответственности и пересобирает UFW-правила из переменных. Для сервера с другими сервисами это нужно менять осознанно.

### Fail2ban

- Ставит и включает fail2ban на хосте.
- Включает jail `sshd`.
- Использует `banaction = ufw`, чтобы fail2ban работал через UFW.
- Настройки jail:
  - `maxretry = 5`
  - `findtime = 10m`
  - `bantime = 1h`

### Logs

- Создает `/etc/systemd/journald.conf.d/99-vpn-limits.conf`.
- Ограничивает persistent journal:
  - `SystemMaxUse = 200M`
  - `SystemKeepFree = 1G`
  - `SystemMaxFileSize = 50M`
  - `RuntimeMaxUse = 50M`
- После изменения лимита перезапускает `systemd-journald`.
- Выполняет `journalctl --vacuum-size`, чтобы старые archived journal files были очищены штатным механизмом journald.
- Управляет `/etc/logrotate.d/btmp`.
- Ротирует `/var/log/btmp` по размеру `25M` и хранит одну старую копию.

Обычные файлы `/var/log/*.log` и `auth.log` не удаляются вручную. Их ротацией управляет `logrotate`.

### Network Tuning

- Создает `/etc/sysctl.d/99-vpn-performance.conf`.
- Включает `net.core.default_qdisc=fq`.
- Включает `net.ipv4.tcp_congestion_control=bbr`.
- Увеличивает `net.netfilter.nf_conntrack_max` до `65536`, чтобы публичное сканирование не переполняло таблицу соединений.
- Перед применением проверяет, что `bbr` доступен в текущем ядре.
- После применения проверяет effective sysctl values.
- Применяет `fq` к активному default-route интерфейсу через `tc`, чтобы настройка работала без ожидания reboot.

Это целесообразно для современного Linux VPS с TCP VPN-трафиком: `fq` дает BBR подходящую очередь пакетов, а `bbr` часто лучше держит пропускную способность и задержку на нестабильных сетях. Это не заменяет правильный выбор протокола/сервера и не ускоряет UDP-трафик напрямую.

### Docker

- Устанавливает Docker CE и compose plugin, если их нет.
- Добавляет официальный Docker apt repository.
- Убирает старый `/etc/apt/sources.list.d/docker.list`, если он конфликтует с новым deb822 repository.
- Настраивает Docker log rotation:
  - `max-size = 10m`
  - `max-file = 3`
- Включает `live-restore`, чтобы контейнеры мягче переживали restart Docker daemon.

### System Updates

- Обновляет apt cache.
- Выполняет безопасное обновление установленных пакетов.
- Делает `autoremove`.
- Выполняет `apt-get clean` через Ansible `apt clean`, чтобы package cache не копил старые `.deb`.
- Не перезагружает сервер автоматически.
- Показывает, требуется ли reboot.

Если после полного прогона есть `Reboot required: True`, запланируй ручную перезагрузку.

### Backups

- Бэкап делается через SQLite online backup API (`sqlite3` `.backup()`) с последующим `PRAGMA integrity_check`, а не сырым копированием файла БД. Это защищает от неконсистентного снимка открытой БД (WAL/journal).
- Удалённый запуск (по SSH): если существует `/opt/vipen/db/x-ui.db`, playbook снимает консистентный snapshot на сервере, скачивает его на управляющую машину в `backups/<host>/<timestamp>/x-ui.db` с правами `0600` (off-host копия) и удаляет серверный snapshot.
- Локальный запуск (`ansible_connection: local`): off-host копия невозможна, поэтому консистентный snapshot остаётся на сервере как `/opt/vipen/backups/x-ui.db.snapshot-<timestamp>` (тот же диск, не off-host).
- На сервере перед изменениями роль `xui` дополнительно сохраняет копии:
  - `/opt/vipen/backups/x-ui.db.bak-<timestamp>`
  - `/opt/vipen/backups/docker-compose.yml.bak-<timestamp>`

#### Бэкап по расписанию

Всё перечисленное выше происходит только когда вы запускаете playbook. Между запусками клиент, заведённый через панель, существует в единственном экземпляре. Поэтому на сервере работает таймер `xui-backup.timer`.

- Снимает тот же консистентный snapshot (`.backup()` + `integrity_check`) в `/opt/vipen/backups/x-ui.db.auto-<timestamp>` с правами `0600`.
- Расписание задаётся `xui_backup_timer_schedule` (формат systemd `OnCalendar`, по умолчанию `daily`), плюс случайная задержка до 15 минут и `Persistent=true` — пропущенный из-за простоя запуск догоняется после загрузки.
- Ротация: хранится `xui_backup_keep` последних снимков (по умолчанию 7), остальные удаляются. **Ротация трогает только файлы `x-ui.db.auto-*`** — копии `x-ui.db.bak-*`, снятые ролью `xui` перед рискованными изменениями, не удаляются никогда.
- Результат пишется в journald с тегом `xui-backup`. При ошибке unit падает и виден в `systemctl --failed`.

Смотреть:

```bash
systemctl list-timers xui-backup.timer
journalctl -t xui-backup --since '7 days ago' -o short-iso --no-pager
ls -la /opt/vipen/backups/
```

Запустить вне расписания:

```bash
systemctl start xui-backup.service
```

Отключить: `xui_backup_timer_enabled: false` и прогон `--tags backup` — playbook остановит и выключит таймер.

**Важно:** это копия на том же диске. Она защищает от повреждения БД, неудачной миграции 3x-ui и случайно удалённого клиента, но **не** от потери самого VPS. Off-host копия по-прежнему появляется только при запуске playbook.

`x-ui.db` содержит клиентов, UUID, пароли и настройки панели. Его нельзя коммитить, отправлять в чат или хранить в публичных backup.

### 3x-ui

- Использует директорию `/opt/vipen`.
- Хранит БД в `/opt/vipen/db`.
- Хранит cert files в `/opt/vipen/cert`.
- Рендерит минимальный `docker-compose.yml`.
- Использует pinned image из `xui_image`.
- Запускает контейнер в `network_mode: host`.
- Добавляет capabilities `NET_ADMIN` и `NET_RAW`, чтобы встроенные механизмы 3x-ui для ban/IP-limit могли управлять сетевыми правилами.
- Сначала запускает контейнер и ждет, пока 3x-ui создаст или мигрирует БД.
- Проверяет, что в БД есть ожидаемые таблицы `settings` и `inbounds`.
- После этого привязывает web panel к `127.0.0.1`.
- Выключает subscription server, если `xui_subscription_enabled: false`.
- Включает subscription server, если `xui_subscription_enabled: true`.
- Сохраняет текущие `subPath`/`subURI` из БД, если `xui_subscription_path` и `xui_subscription_uri` пустые.
- Может восстановить `/opt/vipen/db/x-ui.db` из локального backup.
- Применяет non-secret overrides для VLESS/REALITY inbound: target, serverNames, fingerprint, sniffing.
- Держит `/opt/vipen` и вложенные каталоги закрытыми: `0700`.
- Держит `/opt/vipen/db/x-ui.db` с правами `0600`.
- Проверяет, что локальный порт панели поднялся.

Панель доступна через SSH tunnel:

```bash
ssh -i ~/.ssh/vipen -L <panel-port>:127.0.0.1:<panel-port> root@<your-server-ip>
```

Потом открывайте локально:

```text
http://127.0.0.1:<panel-port>/<panel-path>/
```

### Monitoring

- Устанавливает `/usr/local/sbin/vpn-health-check`.
- Создает `vpn-health.service` и `vpn-health.timer`.
- Запускает health-check каждую минуту.
- Проверяет:
  - контейнер 3x-ui запущен;
  - локальная панель отвечает на TCP;
  - локальные VPN TCP-порты отвечают;
  - процесс Xray есть внутри контейнера;
  - диск и доступная память не ниже порогов.
  - таблица conntrack не заполнена более чем на `75%`;
  - ядро не фиксировало паузу виртуальной машины за последние две минуты.
- Дополнительно логирует информационные метрики (не влияют на статус): `load1` и число `established`-соединений на VPN-портах.
- Пишет результат в journald с tag `vpn-health`.
- При смене состояния отправляет уведомление в Telegram. Разбор `FAIL` и `WARN`, создание бота, настройка Ansible Vault и диагностика — в **[`docs/alerting.md`](alerting.md)**.
- Запускает отдельный `vps-host-monitor.service` с интервалом `5` секунд.
- Он фиксирует только аномалии:
  - CPU steal не ниже `5%` за интервал;
  - задержку выполнения виртуальной машины не меньше `1000 ms`.
- Раз в час пишет краткую сводку с максимальными значениями.
- По умолчанию не перезапускает контейнер автоматически.
- Не пишет отдельные файлы с историей, поэтому рост ограничен лимитом journald `SystemMaxUse`.

Команды просмотра:

```bash
systemctl status vpn-health.timer
systemctl list-timers vpn-health.timer
journalctl -t vpn-health -n 50 --no-pager
journalctl -u vpn-health.service -n 50 --no-pager
journalctl -t vps-host-monitor --since today -o short-iso --no-pager
```

Если нужно включить авто-восстановление, поставьте:

```yaml
vpn_monitor_auto_restart: true
vpn_monitor_failure_threshold: 3
```

Это перезапустит контейнер только после трех подряд failed health-check. Локальный monitor ловит падение/зависание процесса на VPS. Для проблем между телефоном и сервером нужен внешний probe с другой сети.

### Healthcheck

Ручная проверка запускается отдельно:

```bash
ansible-playbook site.yml --tags healthcheck
```

Роль устанавливает `/usr/local/sbin/vpn-healthcheck`, запускает его и пишет последний отчет в:

```text
/var/log/vpn-healthcheck-report.log
```

Проверка падает, если видит:

- `vpn-health.timer` не активен;
- основной `/usr/local/sbin/vpn-health-check` вернул ошибку;
- есть failed systemd units;
- контейнер 3x-ui не запущен или был OOM-killed;
- в последних `vpn-health` логах есть `status=WARN` или `status=FAIL`;
- в последних Docker/container/kernel логах есть критические паттерны, переполнение conntrack или пауза виртуальной машины.

По умолчанию смотрится окно `30 minutes ago` для journald и `30m` для Docker logs. Это специально короткое окно, чтобы старые исправленные ошибки не ломали проверку.

## Обновление 3x-ui

1. Поменяйте `xui_image` в `group_vars/vpn_servers.yml`.
2. Проверьте dry-run:

```bash
ansible-playbook site.yml --tags xui --check --diff
```

3. Примени обновление:

```bash
ansible-playbook site.yml --tags xui
```

БД находится в volume `/opt/vipen/db`, поэтому обновление контейнера сохраняет пользователей, inbound, web path, subscription settings и остальные настройки панели.

## Восстановление на новом сервере

Это основной способ быстро поднять такой же сервер с клиентами и ключами:

```yaml
xui_restore_db_enabled: true
xui_restore_db_local_path: "backups/vipen/YYYYMMDDTHHMMSS/x-ui.db"
xui_restore_db_force: false
```

Потом:

```bash
ansible-playbook site.yml --check --diff
ansible-playbook site.yml
```

Если `/opt/vipen/db/x-ui.db` уже существует, playbook остановится. Для осознанной перезаписи поставьте:

```yaml
xui_restore_db_force: true
```

Перед перезаписью существующая БД сохраняется на сервере в `/opt/vipen/backups/`.

## Откат 3x-ui

Если новый образ не заработал, верните прежнюю версию в `xui_image`, например:

```yaml
xui_image: bigbugcc/3x-ui:v2.8.11
```

Потом запустите:

```bash
ansible-playbook site.yml --tags xui
```

Если проблема в миграции БД, восстанови один из backup:

```bash
ssh -i ~/.ssh/vipen root@<your-server-ip>
cd /opt/vipen
docker compose down
cp /opt/vipen/backups/x-ui.db.bak-YYYYMMDDTHHMMSS /opt/vipen/db/x-ui.db
docker compose up -d
```

## Subscription port

`2096/tcp` нужен только для subscription endpoint 3x-ui. Сейчас он включен для обновления клиентских конфигураций через subscription URL.

Минусы публичного доступа:

- это дополнительная HTTP-точка входа;
- при утечке subscription-ссылки клиентские конфиги можно забрать без входа в панель;
- endpoint может светиться в сканерах, даже если web panel закрыта.

Для безопасной работы:

- держите subscription path длинным и случайным;
- оставляйте `xui_subscription_encrypt: true`;
- не публикуйте subscription URL в чатах и публичных заметках.

`xui_subscription_uri` нужен, чтобы панель сразу копировала внешний URL, даже если сама панель открыта через SSH tunnel на `127.0.0.1`. Это чувствительное значение, потому что содержит subscription path. Лучше получать его из восстановленной БД или задавать через vault/extra-vars.

Если `xui_subscription_uri` пустой, 3x-ui может показать subscription URL с `127.0.0.1`. Для клиента используйте тот же путь, но с внешним адресом сервера:

```text
http://<server-ip>:2096/<subscription-path>/<client-sub-id>
```
