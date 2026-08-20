# agent/agent_api/ — API ноды (FastAPI, :9101)

Слушает **только по туннелю** — хост общается с нодой лишь через AmneziaWG. Трафик самих
сканов/эксплуатации идёт с ноды напрямую в целевую сеть, на хост возвращаются только результаты.

## Модули
| Файл | Ответственность |
|---|---|
| `main.py` | Роуты: `/chunk` (скан), `/role`, `/exploit/check`+`/capture`, `/console/*`, `/update`, `/destroy`, `/health`. |
| `config.py` | Настройки ноды — всё через ENV (пути, порт, туннель, dead-man). |
| `runner.py` | Запуск движка `core.auditor` по чанку, стрим результатов. |
| `roles.py` | Роли (`scanner`/`exploiter`) + донастройка под эксплуатацию. |
| `exploit_runner.py` | Две фазы эксплуатации: `check` (safe) и `capture` (закрепление). |
| `console.py` | Интерактивные PTY-сессии bash (для веб-консоли через хост). |
| `deadman.py` | Dead-man switch: самоуничтожение при потере связи с хостом. |
| `updater.py` | Приём и применение обновлений, доставленных хостом. |

## Ключевые ENV
`AGENT_API_PORT` (9101), `HOST_TUNNEL_IP`, `AWG_IFACE`, `DEADMAN_ENABLED/TIMEOUT/INTERVAL/BOOT_GRACE`,
`AGENT_NAME`, `PINTEST_ROOT`, `AGENT_STATE_DIR`. Полный список — в `config.py`.
