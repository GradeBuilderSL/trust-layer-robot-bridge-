# Архитектура bootstrap-пакета E1

Документ описывает отдельный слой bootstrap для Jetson на Noetix E1.

## Зачем он выделен отдельно

Bootstrap больше не считается просто стартовым shell-скриптом. Это отдельный
операционный слой между оператором и основным стеком Trust Layer.

Его задачи:

- подготовить локальную конфигурацию
- обновить зависимости
- пересобрать нативный helper, если обновился SDK или код
- поднять runtime-стек
- снять стартовую телеметрию

## Место в архитектуре

```text
Оператор
  ->
Bootstrap package
  ->
Trust Layer bridge (:8080)
  ->
e1_server.py (:8083)
  ->
native e1_dds_bridge
  ->
Noetix DDS SDK
  ->
E1
```

## Состав пакета

- `scripts/bootstrap_e1_robot.sh` — точка входа
- `scripts/e1_bootstrap/lib.sh` — общая библиотека и раннер фаз
- `scripts/e1_bootstrap/phases.d/` — расширяемые шаги bootstrap
- `.env.e1.local.example` — шаблон локальной конфигурации
- `scripts/start_e1_stack.sh` — отдельный раннер полного стека
- `scripts/e1_collect_telemetry.py` — отдельный сборщик startup telemetry

## Принцип расширения

Bootstrap расширяется не через редактирование одного большого файла, а через
новые фазы.

Правила:

1. добавить новый файл в `scripts/e1_bootstrap/phases.d/`
2. выбрать номер по месту в порядке запуска
3. использовать переменные и функции из `lib.sh`
4. включать и выключать фазу через `.env.e1.local`, если шаг опционален

## Текущие фазы

- `10_env.sh` — summary и базовый env
- `20_python.sh` — Python зависимости
- `30_speech.sh` — локальный speech runtime
- `40_helper.sh` — сборка native helper
- `50_stack.sh` — старт всего стека
- `60_telemetry.sh` — снимок телеметрии, если стек не стартовал в той же команде

## Runtime-артефакты

Все рабочие результаты bootstrap складываются в `runtime/e1/`:

- `logs/`
- `pids/`
- `reports/`

Это позволяет:

- безопасно обновлять пакет
- не смешивать runtime с исходниками
- повторно запускать bootstrap без ручной чистки
