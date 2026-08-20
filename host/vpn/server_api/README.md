# host/vpn/server_api/ — control-API VPN-сервера

Python-пакет управления AmneziaWG-сервером. `awg.py` — операции с `awg0` (up/пиры/ключи),
server-info и **автоопределение адреса хоста** (`resolved_endpoint`). `main.py` — HTTP-роуты
для backend (`/status`, `/server-info`, `/genkeys`, `/peer`). Подробности — в
[`../README.md`](../README.md).
