# host/vpn/ — AmneziaWG-сервер + control-API

Отдельный контейнер с VPN-сервером (по ТЗ VPN — самостоятельная роль). Поднимает
обфусцированный туннель **AmneziaWG** (userspace `amneziawg-go`, интерфейс `awg0=10.9.0.1`)
и даёт backend'у внутренний control-API (`:8080`) для управления пирами/ключами.

Агенты — это **клиенты**, которые дозваниваются к серверу (инвертированный туннель): хост
может быть за NAT, важно лишь, чтобы был открыт `AWG_LISTEN_PORT/udp`.

## Структура
| Файл | Что это |
|---|---|
| `server_api/awg.py` | Управление `awg0` (up/пиры/ключи), server-info, **автоопределение адреса хоста**, `ensure_forwarding` (NAT MASQUERADE `10.9.0.0/24`→WAN для full-tunnel админ-клиентов). |
| `server_api/main.py` | HTTP control-API для backend: `/status`, `/server-info`, `/genkeys`, `/peer`, `/reload` (перечитать awg0 после restore бэкапа). |
| `entrypoint.sh` | Поднять `awg0` (+ NAT) + запустить control-API. |

**Интернет через VPN (full-tunnel):** админ-конфиги по умолчанию гонят в туннель и вебку, и
интернет — `ensure_forwarding()` в `up()` NAT-ит трафик туннеля в WAN. Агентам — split (только
`10.9.0.0/24`). См. [`../../docs/architecture.md`](../../docs/architecture.md) §11.

## Автоопределение адреса (endpoint)
Клиент-конфигу агента нужен адрес, куда звонить. Раньше его хардкодили в `AWG_ENDPOINT`.
Теперь `resolved_endpoint()` определяет его сам: `AWG_ENDPOINT` (если задан) → внешний сервис
(реальный публичный IP за NAT) → IP исходящего интерфейса. Тумблеры: `AWG_NO_EXTERNAL=1`
(закрытый лаб без интернета), `AWG_PUBIP_URLS` (свои сервисы), `AWG_ENDPOINT` (жёсткий override).

Идентичность сервера (ключи + обфускация-параметры «амнезия») хранится на volume
(`/etc/amnezia/amneziawg/server.json`), переживает пересоздание контейнера.
