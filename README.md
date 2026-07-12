# Vipen

**Minimal Ansible stack for a self-hosted 3x-ui VPN.**

Минимальный, воспроизводимый Ansible-пайплайн для VPS с VPN-панелью [3x-ui](https://github.com/MHSanaei/3x-ui): защита сервера, Docker, сетевой tuning, off-host бэкапы БД и лёгкий мониторинг стабильности.

Пайплайн настраивает сервер и разворачивает сам сервис 3x-ui, но **не создаёт VPN-подключения и пользователей** — вы их заводите в панели вручную или восстанавливаете из бэкапа БД. Секреты клиентов не хранятся в репозитории.

## Возможности

- 🔒 **Хардненинг** — SSH только по ключу, UFW, fail2ban, закрытая панель (доступ через SSH-туннель).
- 🐳 **Docker + 3x-ui** — pinned-образ, ротация логов, `live-restore`, авто-сохранение БД при обновлении.
- ⚡ **Сетевой tuning** — `fq` + BBR, увеличенный `nf_conntrack_max`.
- 💾 **Бэкапы** — off-host копия `x-ui.db` на локальную машину, восстановление на новый сервер одной переменной.
- 📊 **Мониторинг** — health-check раз в минуту, монитор гипервизора (CPU steal / задержки VM), ручная диагностика.

## Требования

- **Локально:** Ansible (core ≥ 2.16), Python 3, SSH-ключ до сервера.
- **Сервер:** свежий VPS на Debian/Ubuntu с доступом `root` по SSH-ключу.

## Быстрый старт

Выберите свой сценарий. Всё настраивается в одном файле — `inventories/production/group_vars/vpn_servers.yml`. Команды запускаются из корня репозитория.

### Сценарий 1. Удалённо — с вашего компьютера по SSH

```bash
cp inventories/production/hosts.example.yml \
   inventories/production/hosts.yml
# впишите в hosts.yml IP сервера и путь к SSH-ключу, затем:
ansible-playbook site.yml --check --diff   # предпросмотр (dry-run)
ansible-playbook site.yml                   # применить
```

Подробнее — в разделах [«Перед первым запуском»](docs/setup.md#перед-первым-запуском) и [«Применение»](docs/setup.md#применение).

### Сценарий 2. Локально — на самом сервере

На сервере уже установлены `ansible-core` и `python3`, запуск от root:

```bash
cp inventories/production/hosts.local.example.yml \
   inventories/production/hosts.local.yml
# впишите в hosts.local.yml свой публичный SSH-ключ (ssh_authorized_keys), затем:
ansible-playbook -i inventories/production/hosts.local.yml site.yml --check --diff
ansible-playbook -i inventories/production/hosts.local.yml site.yml
```

Отличается только флаг `-i ...hosts.local.yml` (он переопределяет инвентарь по умолчанию — `ansible.cfg` менять не нужно). Бэкап при этом делается on-host, а не off-host.

Подробнее — в разделе [«Локальный запуск (на самом сервере)»](docs/setup.md#локальный-запуск-на-самом-сервере).

## Документация

📖 **Полная инструкция по настройке, ролям и сценариям — в [`docs/setup.md`](docs/setup.md).**

Там разобраны: центральный конфиг, доступ к панели, обновление и откат 3x-ui, восстановление на новом сервере, мониторинг и метрики, firewall/SSH.

Рабочий регламент и диагностические сценарии — в [`AGENTS.md`](AGENTS.md).

## Безопасность

`.gitignore` исключает секреты: `x-ui.db`, локальные бэкапы, реальный инвентарь (`hosts.yml`), `server.txt`. Не добавляйте в `group_vars` открытым текстом UUID клиентов, REALITY-ключи и subscription path/URI — они хранятся в базе.
