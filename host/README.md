# host/ — control plane (реальный деплой)

Хостовый сервер («мозг» системы): вебка управляет агентами, раздаёт задания, собирает
отчёты и лут. Сюда же ставится хардненинг. Это **продуктовый** код для реального сервера —
`lab/` лишь инстанцирует те же образы для симуляции на своём ПК.

## Развёртывание на реальном Ubuntu
```bash
git clone <repo> pintest && cd pintest/host
sudo bash install.sh     # Docker + ufw/fail2ban + PG17 + стек; --fresh = чистый старт
```
Цветной пошаговый вывод (лог — `/tmp/pintest-setup.log`). **Спросит SSH-порт** (Enter — текущий;
другой — безопасно перенесёт sshd, без лок-аута). Адрес хоста (`AWG_ENDPOINT`) **определяется сам**
— вручную только при сложном NAT. Вебка — по VPN на `https://<host>` (без порта); bootstrap-конфиг
в `host/data/bootstrap-admin.conf`. Дальше подключаешь агентов из вебки (вкладка «Агенты»).

## Что внутри
| Папка / файл | Что это |
|---|---|
| [`backend/`](backend/) | FastAPI control plane — вся логика (оркестрация, provisioning, VPN, эксплуатация, лут, отчёты). |
| [`frontend/`](frontend/) | nginx + статика вебки (дашборд, живая топология-граф, консоль узлов). |
| [`vpn/`](vpn/) | AmneziaWG-сервер + внутренний control-API (пиры/ключи/endpoint). |
| [`fail2ban/`](fail2ban/) | Защита хоста от перебора SSH (в реале ставится на сам сервер). |
| `docker-compose.yml` | Стек хоста для реального сервера. |
| `install.sh` | Bootstrap: Docker + хардненинг + .env + запуск. |
| `.env.example` | Шаблон конфигурации (копируется в `.env`; всё поведение — через ENV). |

## Ключевые ENV (полный список — в `.env.example` и `backend/app/config.py`)
`ADMIN_USER/ADMIN_PASSWORD` (UPSERT на каждом старте), `SESSION_SECRET`, `SSH_PORT` (хардненинг),
`AWG_ENDPOINT` (опц., авто), `AWG_LISTEN_PORT`, `ADMIN_VPN_FULL_TUNNEL`/`ADMIN_VPN_DNS` (интернет
через VPN), `BACKUP_KEEP`, `CHUNK_SIZE`, `HEARTBEAT_INTERVAL/MISS`, `LIVE_INTERVAL`, `DB_DSN`, `DATA_DIR`.

Архитектура целиком — в [../docs/architecture.md](../docs/architecture.md).
