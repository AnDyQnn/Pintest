# target: vsftpd 2.3.4 (CVE-2011-2523)

Симуляция FTP-сервиса с **бэкдором vsftpd 2.3.4**: логин, содержащий `:)`, открывает рут-шелл
на TCP/6200. `server.py` эмулирует баннер `220 (vsFTPd 2.3.4)` и бэкдор-шелл с флагом.

Модуль эксплуатации: [`../../../exploits/modules/vsftpd_2323.py`](../../../exploits/modules/vsftpd_2323.py).
Поднимается в `targets_net` (см. [`../../docker-compose.yml`](../../docker-compose.yml)).
