# Подключение Noetix E1 к Trust Layer

Короткая операторская памятка для живой работы с E1 на Jetson Orin.

## Что уже автоматизировано

- `scripts/bootstrap_e1_robot.sh` готовит Jetson одной командой
- `scripts/e1_bootstrap/` выделен как отдельный bootstrap-пакет
- `scripts/start_e1_stack.sh` поднимает оба процесса стека
- `scripts/e1_collect_telemetry.py` снимает стартовую телеметрию
- `.env.e1.local` позволяет хранить локальные настройки без ручного редактирования скриптов

## Один вход для нового и старого робота

```bash
cd /home/noetix/trust-layer-robot-bridge-
bash scripts/bootstrap_e1_robot.sh
```

После этого проверяем:

```bash
curl -s http://127.0.0.1:8083/health
curl -s http://127.0.0.1:8083/api/state
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/brain/status
```

## Что смотреть в `/api/state`

Теперь помимо обычных полей сервер отдаёт:

- `status_received`
- `workmode_raw`
- `joy_axes`
- `imu`
- `motors_count`
- `last_status_timestamp_us`

Это помогает сразу понять, пришёл ли реальный `Robot_Status_Topic` или
сервер ещё живёт на заглушках.

## Где лежат результаты старта

- логи: `runtime/e1/logs/`
- pid-файлы: `runtime/e1/pids/`
- снимки телеметрии: `runtime/e1/reports/telemetry_*.json`

## Как обновлять bootstrap-пакет

Bootstrap теперь расширяется через `scripts/e1_bootstrap/phases.d/`.

Это значит:

- новые подготовительные шаги добавляются как отдельные фазы
- существующие шаги можно менять без переписывания точки входа
- операторская команда запуска остаётся прежней

## Если стек уже настроен и нужен только быстрый старт

```bash
cd /home/noetix/trust-layer-robot-bridge-
bash scripts/start_e1_stack.sh
```

## Если нужен только повторный снимок телеметрии

```bash
cd /home/noetix/trust-layer-robot-bridge-
python3 scripts/e1_collect_telemetry.py \
  --robot-url http://127.0.0.1:8083 \
  --bridge-url http://127.0.0.1:8080 \
  --output-dir runtime/e1/reports
```

## Подтверждённый рабочий минимум

- `e1_server` отвечает на `:8083`
- `bridge.main` отвечает на `:8080`
- `native/build/e1_dds_bridge` собирается и стартует
- DDS publish path подтверждён по логам helper
- TTS на Jetson работает через `espeak-ng`
- SSH по Wi-Fi работает после отключения client isolation

## Что пока не подтверждено

- стабильное чтение `Robot_Status_Topic` во всех сценариях
- реальное выполнение high-level gesture/action команд роботом
- безопасный single-joint arm control через `Motor_Cmd_Topic`

## Wi-Fi

Правило для полевой работы:

- не использовать guest Wi-Fi без отключения client isolation
- сначала проверить SSH и `/health` по Wi-Fi
- только потом убирать LAN

Команды:

```bash
sudo nmcli dev wifi list
sudo nmcli dev wifi connect "<SSID>" password "<WIFI_PASSWORD>"
hostname -I
ssh noetix@<wifi-ip>
curl http://<wifi-ip>:8083/health
```

## Речь

```bash
espeak-ng -v ru "Привет. Я робот E1."
curl -s -X POST "http://127.0.0.1:8083/api/audio/speak" \
  -H "Content-Type: application/json" \
  -d '{"text":"Привет. Я робот E1.","lang":"ru"}'
```

## Когда надо останавливаться и разбираться глубже

Если одновременно верно всё ниже:

- `/health` зелёный
- helper логирует `publish_control`
- `/api/state` не получает `status_received=true`
- робот физически не реагирует

значит проблема, вероятнее всего, уже ниже Trust Layer: в DDS status path,
режиме робота или в нижнем locomotion/stabilization слое SDK.
