# E1 Bootstrap Package

Модульный bootstrap-пакет для Jetson на Noetix E1.

## Идея

`scripts/bootstrap_e1_robot.sh` теперь только точка входа. Реальная логика
разбита на фазы в `scripts/e1_bootstrap/phases.d/`.

Это даёт три свойства:

- пакет легко пополнять новыми шагами
- существующие шаги можно обновлять независимо
- операторский вход остаётся тем же: одна команда

## Структура

- `lib.sh` — общее окружение и раннер фаз
- `phases.d/10_env.sh` — локальный env уже загружен и выводит summary
- `phases.d/20_python.sh` — Python зависимости
- `phases.d/30_speech.sh` — локальный TTS runtime
- `phases.d/40_helper.sh` — сборка native DDS helper
- `phases.d/50_stack.sh` — запуск `e1_server` и `bridge.main`
- `phases.d/60_telemetry.sh` — стартовый снимок телеметрии

## Как расширять

Чтобы добавить новый шаг:

1. создать новый файл в `phases.d/`, например `35_network.sh`
2. использовать `bootstrap_log` и переменные из `lib.sh`
3. выбрать номер так, чтобы фаза попала в нужное место порядка запуска

Пример:

```bash
#!/usr/bin/env bash
if [ "${E1_BOOTSTRAP_MY_PHASE:-0}" != "1" ]; then
    exit 0
fi
bootstrap_log "my custom phase"
```

## Как отключать фазы

Через `.env.e1.local`:

- `E1_BOOTSTRAP_INSTALL_DEPS=0`
- `E1_BOOTSTRAP_INSTALL_SPEECH=0`
- `E1_BOOTSTRAP_BUILD_HELPER=0`
- `E1_BOOTSTRAP_START_STACK=0`
- `E1_BOOTSTRAP_COLLECT_TELEMETRY=0`
