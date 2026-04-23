# Установка и старт Trust Layer Bridge на роботе

Актуальная инструкция для Jetson на Noetix E1. Документ рассчитан и на новый
робот, и на тот, который уже был в работе: вместо пошаговой ручной настройки
используется один и тот же idempotent bootstrap.

## Что нужно заранее

- доступ по SSH к Jetson
- репозиторий `trust-layer-robot-bridge-` на Jetson
- SDK `noetix_sdk_e1` рядом с репозиторием или в одном из стандартных путей
- Python 3.8+

## Базовый путь для нового и существующего E1

```bash
cd /home/noetix/trust-layer-robot-bridge-
bash scripts/bootstrap_e1_robot.sh
```

Что делает bootstrap:

- создаёт `.env.e1.local` из `.env.e1.local.example`, если локального файла ещё нет
- ставит зависимости из `requirements.txt`
- находит `noetix_sdk_e1`
- пересобирает `native/e1_dds_bridge`, если доступен SDK и `cmake`
- запускает `e1_server` на `:8083`
- запускает `bridge.main` на `:8080`
- снимает стартовый снимок телеметрии и сохраняет его в `runtime/e1/reports/`

Отдельная архитектура bootstrap-пакета описана в:

- [E1_BOOTSTRAP_ARCHITECTURE.md](E1_BOOTSTRAP_ARCHITECTURE.md)

## Что хранится после bootstrap

- логи: `runtime/e1/logs/e1_server.log`, `runtime/e1/logs/bridge.log`
- pid-файлы: `runtime/e1/pids/`
- телеметрия старта: `runtime/e1/reports/telemetry_*.json`

## Если нужно только поднять стек повторно

```bash
cd /home/noetix/trust-layer-robot-bridge-
bash scripts/start_e1_stack.sh
```

## Если нужно только переснять телеметрию

```bash
cd /home/noetix/trust-layer-robot-bridge-
python3 scripts/e1_collect_telemetry.py \
  --robot-url http://127.0.0.1:8083 \
  --bridge-url http://127.0.0.1:8080 \
  --output-dir runtime/e1/reports
```

## Проверки после старта

```bash
curl -s http://127.0.0.1:8083/health
curl -s http://127.0.0.1:8083/api/state
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/brain/status
```

## Настройки через `.env.e1.local`

Локальный файл читается автоматически:

- `scripts/start_e1_server.sh`
- `scripts/start_e1_bridge.sh`
- `scripts/start_e1_stack.sh`
- `scripts/bootstrap_e1_robot.sh`

Основные переменные:

- `E1_TRANSPORT=noetix_dds`
- `E1_SERVER_PORT=8083`
- `BRIDGE_PORT=8080`
- `ROBOT_URL=http://127.0.0.1:8083`
- `E1_BOOTSTRAP_INSTALL_DEPS=1`
- `E1_BOOTSTRAP_BUILD_HELPER=1`
- `E1_BOOTSTRAP_START_STACK=1`
- `E1_BOOTSTRAP_COLLECT_TELEMETRY=1`
- `E1_BOOTSTRAP_INSTALL_SPEECH=0`

## Пакет bootstrap как обновляемый слой

Bootstrap теперь модульный. Его внутренняя структура:

- `scripts/bootstrap_e1_robot.sh` — точка входа
- `scripts/e1_bootstrap/lib.sh` — общая библиотека
- `scripts/e1_bootstrap/phases.d/` — нумерованные фазы

Это позволяет:

- добавлять новые шаги без переписывания entrypoint
- обновлять отдельные части пакета независимо
- поддерживать один и тот же способ запуска для нового и старого робота

## Wi-Fi

Подтверждённый сценарий:

```bash
sudo nmcli dev wifi list
sudo nmcli dev wifi connect "<SSID>" password "<WIFI_PASSWORD>"
hostname -I
```

Проверки:

```bash
ping -c 1 8.8.8.8
sudo systemctl status ssh --no-pager
ss -ltnp | grep :22
```

Важно:

- guest-сеть может давать интернет, но блокировать SSH и HTTP между клиентами
- если используется guest Wi-Fi, нужно отключить client isolation / AP isolation
- LAN лучше отключать только после проверки SSH и `/health` по Wi-Fi

## Речь на Jetson

Подтверждённый минимальный вариант:

```bash
sudo apt update
sudo apt install -y espeak-ng ffmpeg
espeak-ng -v ru "Привет. Я робот E1."
```

Через `e1_server`:

```bash
curl -s -X POST "http://127.0.0.1:8083/api/audio/speak" \
  -H "Content-Type: application/json" \
  -d '{"text":"Привет. Я робот E1.","lang":"ru"}'
```
