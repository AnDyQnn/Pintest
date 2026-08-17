# awg-client — клиентская сторона AmneziaWG на агенте.

Конфиг awg0.conf генерирует и вбрасывает ХОСТ по SSH при provisioning (`agent/scripts/apply-awg.sh`).
Здесь только пример формата (`awg0.conf.example`). Туннель поднимается через `awg-quick up awg0`
(userspace `amneziawg-go`, см. `awg-base/`).
