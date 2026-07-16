[Русский](README.md) | [English](README.en_EN.md)

# Vipen

[![Lint](https://github.com/osipovgit/vipen/actions/workflows/ci.yml/badge.svg)](https://github.com/osipovgit/vipen/actions/workflows/lint.yml)

Ansible-пайплайн, который на Debian/Ubuntu-хосте разворачивает [3x-ui](https://github.com/MHSanaei/3x-ui) (панель управления + VPN-сервер Xray).

При этом пайплайн **не создаёт VPN-подключения и пользователей** — вы заводите их в панели вручную или восстанавливаете из бэкапа БД.

## Что настраивает

- 🔒 **Защита сервера** — SSH только по ключу, UFW, fail2ban, закрытая панель (доступ через SSH-туннель).
- 🐳 **Docker-окружение** — ротация логов, `live-restore`, снимок БД перед обновлением.
- ⚡ **Сетевая оптимизация** — `fq` + BBR, увеличенный `nf_conntrack_max`.
- 💾 **Бэкапы** — копия `x-ui.db` на локальную машину, восстановление на новом сервере.
- 📊 **Мониторинг** — ежеминутный health-check, признаки перегрузки гипервизора (CPU steal, задержки VM), инструменты для ручной диагностики.

## Требования

- **Целевой сервер:** Debian/Ubuntu с доступом `root` по SSH-ключу.
- **Машина, с которой запускаете:** Ansible (core ≥ 2.16), Python 3. При удалённом запуске — SSH-ключ для доступа к серверу.

## Быстрый старт

Пайплайн запускается двумя способами: удалённо по SSH с вашей машины или локально на самом сервере.

Параметры пайплайна собраны в одном файле — `inventories/production/group_vars/vpn_servers.yml`; данные подключения к серверу задаются в инвентаре.

Не вписывайте в `group_vars` UUID клиентов, ключи REALITY и ссылки на подписку — их место в базе, и при публичном форке они утекут.

**Команды запускаются из корня репозитория.**

### Сценарий 1. Удалённо по SSH

```bash
cp inventories/production/hosts.example.yml \
   inventories/production/hosts.yml
# впишите в hosts.yml IP сервера и путь к SSH-ключу, затем:
ansible-playbook site.yml --check --diff    # предпросмотр (dry-run)
ansible-playbook site.yml                   # применить
```

Подробнее — в разделах [«Перед первым запуском»](docs/setup.md#перед-первым-запуском) и [«Применение»](docs/setup.md#применение).

### Сценарий 2. Локально на сервере

Требуется, чтобы на сервере были установлены `ansible-core` и `python3`; запуск от root.

```bash
cp inventories/production/hosts.local.example.yml \
   inventories/production/hosts.local.yml
# впишите в hosts.local.yml свой публичный SSH-ключ (ssh_authorized_keys), затем:
ansible-playbook -i inventories/production/hosts.local.yml site.yml --check --diff  # предпросмотр (dry-run)
ansible-playbook -i inventories/production/hosts.local.yml site.yml                 # применить
```

Бэкап `x-ui.db` при этом остаётся на самом сервере — отдельной копии вне хоста не будет.

Подробнее — в разделе [«Локальный запуск (на самом сервере)»](docs/setup.md#локальный-запуск-на-самом-сервере).

## Документация

**Полная инструкция по настройке, ролям и сценариям — в [`docs/setup.md`](docs/setup.md).**

Там разобраны: параметры пайплайна, доступ к панели, обновление и откат 3x-ui, восстановление на новом сервере, мониторинг и метрики, UFW и SSH.

Рабочий регламент и диагностические сценарии — в [`AGENTS.md`](AGENTS.md).
