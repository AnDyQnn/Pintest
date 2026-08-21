# docs/ — документация проекта

| Документ | О чём |
|---|---|
| [`quickstart-wsl.md`](quickstart-wsl.md) | **Прогон с нуля на WSL** — пошагово, с проверками на каждом шаге и чеклистом демонстрации. Начни отсюда. |
| [`architecture.md`](architecture.md) | Архитектура: хост/агенты, netns-модель, VPN (full-tunnel), полный цикл, граф/достижимость, консоль, лут, эксплуатация, dead-man, бэкапы, обновления. Диаграммы — mermaid. |
| [`schema.dbml`](schema.dbml) | **Схема БД** (PostgreSQL 17) в формате DBML — вставь на [dbdiagram.io](https://dbdiagram.io) для ER-диаграммы. Краткая ER-версия — в `architecture.md`. |
| [`usage.md`](usage.md) | Инструкция от установки до работы: лаба на своём ПК **(A)** и реальные серверы **(B)** + все механики. |
| [`exploitation.md`](exploitation.md) | **Движок эксплуатации**: real-time автозахват (модуль «на лету»), self-spreading через плацдарм, как добавить CVE/устройство строкой каталога, эндпоинты. |

С чего начать новичку — [`../START.md`](../START.md). Обзор папок — [`../README.md`](../README.md).
Ручной прогон лабы по шагам — [`../lab/README.md`](../lab/README.md).
