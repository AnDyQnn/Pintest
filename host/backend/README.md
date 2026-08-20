# host/backend/ — FastAPI control plane

Вся серверная логика системы. Делит сетевой namespace с контейнером `vpn`
(`network_mode: service:vpn`), поэтому напрямую видит туннель и достаёт агентов по `10.9.0.X`.

## Модули (`app/`)
| Файл | Ответственность |
|---|---|
| `main.py` | Все HTTP-роуты `/api/*` + WebSocket `/api/live` (живой статус вебки). |
| `config.py` | Настройки — всё через ENV (переносимость лаба↔реал). |
| `db.py` | Тонкий слой над PostgreSQL (psycopg3): агенты, джобы, чанки, находки, захваты. |
| `agents.py` | Реестр агентов, provisioning, heartbeat, роли. |
| `provisioner.py` | SSH (paramiko): вход на ноду, вброс AWG-ключа, поднятие туннеля. |
| `vpn.py` | Управление VPN-сервером (пиры/ключи/клиент-конфиги). |
| `orchestrator.py` | Нарезка на чанки, раздача агентам, failover, слияние отчёта. |
| `targets.py` | Приём и чистка «грязного» списка целей. |
| `exploitation.py` | Координация эксплуатации: check (safe) + capture (по подтверждению). |
| `loot.py` | Сбор лута из успешных закреплений + лут-отчёт (md/html/json). |
| `topology.py` | Сеть как граф: узлы/рёбра, достижимость, рокировка, точки отказа. |
| `console.py` | Проброс интерактивной консоли к агенту (через туннель). |
| `reports.py` / `diff.py` | Доступ к отчётам / сравнение прогонов. |
| `backup.py` / `updates.py` | Бэкапы / две механики обновлений (хост сам, агентам — с хоста). |
| `users.py` | Учётные записи вебки. |

## Запуск
Через `docker compose` (см. [../docker-compose.yml](../docker-compose.yml) или [../../lab/](../../lab/)).
Локально: `uvicorn app.main:app` с выставленными ENV из [../config.example.env](../config.example.env).
