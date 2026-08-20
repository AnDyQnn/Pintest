# target: Apache 2.4.49 (CVE-2021-41773)

Симуляция Apache 2.4.49 с **path traversal → RCE** (`.%2e/`): обход каталога позволяет выполнить
`/bin/sh`. `server.py` эмулирует уязвимый ответ и снятие флага через RCE.

Модуль эксплуатации: [`../../../exploits/modules/apache_41773.py`](../../../exploits/modules/apache_41773.py).
Поднимается в `targets_net` (см. [`../../docker-compose.yml`](../../docker-compose.yml)).
