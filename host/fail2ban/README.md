# host/fail2ban/ — защита от перебора SSH (host + агенты)

**Это НЕ VPN/Amnezia** (тот в [`../vpn/`](../vpn/)). fail2ban — слой хардненинга: читает журнал
sshd и **банит IP** после нескольких неудачных входов. Не контейнер — системный сервис.

## Файл
| Файл | Что это |
|---|---|
| `jail.local` | Политика: `maxretry=5`, `findtime=10m`, `bantime=1h`, `banaction=iptables-allports`; jail `[sshd]` с `backend=systemd` (sshd на совр. Ubuntu пишет в **journald**, а НЕ в `/var/log/auth.log`); `ignoreip` — все приватные сети (админ из VPN/LAN себя не забанит). |

## Где ставится
- **Хост:** `host/install.sh` ставит `fail2ban` + `python3-systemd` на сам сервер, копирует сюда
  `jail.local`, поднимает `ufw` (firewall) под выбранный SSH-порт.
- **Агент-нода:** `agent/scripts/deploy.sh` ставит тот же fail2ban+ufw на агент-сервер под её
  реальный SSH-порт (от root). Отключить: `AGENT_HARDEN=0`.

Требует `python3-systemd` — без него `backend=systemd` не стартует (jail «не активен»).
