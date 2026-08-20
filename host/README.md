# host/ — control plane (реальный деплой)

Хостовый сервер («мозг» системы): вебка управляет агентами, раздаёт задания, собирает
отчёты и лут. Сюда же ставится хардненинг. Это **продуктовый** код для реального сервера —
`lab/` лишь инстанцирует те же образы для симуляции на своём ПК.

## Развёртывание на реальном Ubuntu
```bash
git clone <repo> pintest && cd pintest/host
sudo ./install.sh        # ставит Docker + ufw/fail2ban, создаёт config.env, поднимает стек
```
Адрес хоста (`AWG_ENDPOINT`) **определяется сам** — прописывать руками нужно только при сложном
NAT. Дальше подключаешь агентов из вебки (вкладка «Агенты»), они ставятся сами по SSH.

## Что внутри
| Папка / файл | Что это |
|---|---|
| [`backend/`](backend/) | FastAPI control plane — вся логика (оркестрация, provisioning, VPN, эксплуатация, лут, отчёты). |
| [`frontend/`](frontend/) | nginx + статика вебки (дашборд, живая топология-граф, консоль узлов). |
| [`vpn/`](vpn/) | AmneziaWG-сервер + внутренний control-API (пиры/ключи/endpoint). |
| [`fail2ban/`](fail2ban/) | Защита хоста от перебора SSH (в реале ставится на сам сервер). |
| `docker-compose.yml` | Стек хоста для реального сервера. |
| `install.sh` | Bootstrap: Docker + хардненинг + config.env + запуск. |
| `config.example.env` | Шаблон конфигурации (копируется в `config.env`; всё поведение — через ENV). |

## Ключевые ENV (полный список — в `config.example.env` и `backend/app/config.py`)
`ADMIN_USER/ADMIN_PASSWORD`, `SESSION_SECRET`, `AWG_ENDPOINT` (опц., авто), `AWG_LISTEN_PORT`,
`CHUNK_SIZE`, `HEARTBEAT_INTERVAL/MISS`, `LIVE_INTERVAL`, `DB_DSN`, `DATA_DIR`.

Архитектура целиком — в [../docs/architecture.md](../docs/architecture.md).
