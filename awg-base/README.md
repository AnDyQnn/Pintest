# awg-base/ — общий образ AmneziaWG (userspace)

Базовый Docker-образ `pintest-awg-base` со сборкой **userspace-реализации AmneziaWG**
(`amneziawg-go` + утилиты `awg`/`awg-quick`). Из него наследуются образы `vpn` (сервер) и
`agent` (клиент) — чтобы одинаковый обфусцированный туннель работал и там, и там без kernel-модуля
(важно в контейнерах/WSL, где модуля ядра нет).

## Файлы
- `Dockerfile` — компиляция `amneziawg-go` из исходников + установка утилит.

## Сборка
Собирается один раз перед стеком (см. [`../lab/build.sh`](../lab/build.sh) и
[`../host/install.sh`](../host/install.sh)):
```bash
docker build -t pintest-awg-base:latest awg-base
```
Первая сборка ~2–4 мин (компиляция Go), дальше кешируется.
