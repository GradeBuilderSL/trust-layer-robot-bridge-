# Подключение Noetix E1 к Trust Layer

Актуальная операторская памятка по живой отладке E1 на Jetson Orin.

## Что подтверждено

- `bridge/e1_server.py` запускается и отвечает на `:8083`
- `native/build/e1_dds_bridge` собирается и стартует
- publish path в DDS подтвержден
- `/api/cmd/vel`, `/api/cmd/stop`, `/api/cmd/gesture` доходят до helper
- локальная речь через `espeak-ng` работает
- SSH по Wi-Fi работает после отключения client isolation на роутере

## Что пока не подтверждено

- стабильное чтение `Robot_Status_Topic` в helper
- реальная реакция робота на high-level DDS gesture/action команды
- safe single-joint arm control через `Motor_Cmd_Topic`

## Актуальная схема

```text
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

## Пути на Jetson

- репозиторий bridge: `/home/noetix/trust-layer-robot-bridge-`
- SDK Noetix: `/home/noetix/noetix_sdk_e1`
- native helper: `/home/noetix/trust-layer-robot-bridge-/native/build/e1_dds_bridge`

## Запуск E1 server

```bash
cd /home/noetix/trust-layer-robot-bridge-
E1_TRANSPORT=noetix_dds bash scripts/start_e1_server.sh
```

Проверка:

```bash
curl -s http://127.0.0.1:8083/health
curl -s http://127.0.0.1:8083/api/state
```

## Запуск Trust Layer bridge

```bash
cd /home/noetix/trust-layer-robot-bridge-
ADAPTER_TYPE=e1 ROBOT_URL=http://127.0.0.1:8083 BRIDGE_PORT=8080 python3 -m bridge.main
```

Проверка:

```bash
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/brain/status
curl -s -X POST "http://127.0.0.1:8080/chat" \
  -H "Content-Type: application/json" \
  -d '{"message":"Привет, как тебя зовут?"}'
```

## Рабочие команды E1 server

```bash
curl -s -X POST "http://127.0.0.1:8083/api/cmd/vel?vx=0.05&vyaw=0.0"
curl -s -X POST "http://127.0.0.1:8083/api/cmd/stop"
curl -s -X POST "http://127.0.0.1:8083/api/cmd/gesture" \
  -H "Content-Type: application/json" \
  -d '{"name":"wave","slot":"preset_a"}'
```

Важно:

- маршрут `cmd_vel` добавлен как alias к `walk`
- helper ожидает compact JSON без лишних пробелов
- `transport_error` в `/health` должен быть `null`

## Локальная речь на Jetson

Подтвержденный быстрый путь:

```bash
sudo apt update
sudo apt install -y espeak-ng ffmpeg
```

Проверка напрямую:

```bash
espeak-ng --voices | grep -i ru
espeak-ng -v ru "Привет. Я робот E1. Русский голос работает."
```

Проверка через `e1_server`:

```bash
curl -s -X POST "http://127.0.0.1:8083/api/audio/speak" \
  -H "Content-Type: application/json" \
  -d '{"text":"Привет. Я робот E1. Русский голос работает.","lang":"ru"}'
```

Ожидаемый ответ:

```json
{"status":"ok","method":"espeak","spoken":"Привет. Я робот E1. Русский голос работает."}
```

## Минимальный диалоговый режим

- `e1_server` на `:8083`
- `bridge.main` на `:8080`
- `scripts/e1_dialog.py` отправляет текст в `/chat`, а ответ озвучивает через `/api/audio/speak`

Запуск:

```bash
cd /home/noetix/trust-layer-robot-bridge-
python3 scripts/e1_dialog.py
```

## Wi-Fi: важные нюансы

Подключение к guest-сети может давать интернет, но блокировать SSH и HTTP между устройствами.

Что помогло:

```bash
sudo nmcli dev wifi list
sudo nmcli dev wifi connect "KUPIROBOT" password "Kupirobot"
hostname -I
```

Проверки:

```bash
ping -c 1 8.8.8.8
sudo systemctl status ssh --no-pager
ss -ltnp | grep :22
```

Если используется guest Wi-Fi:

- отключить client isolation / AP isolation на роутере
- после этого проверить SSH по Wi-Fi
- только потом отключать LAN

Проверка с операторского ноутбука:

```bash
ssh noetix@<wifi-ip>
curl http://<wifi-ip>:8083/health
```

## Полезные диагностические команды

```bash
ss -ltnp | grep 8080
ss -ltnp | grep 8083
ps -ef | grep e1_server.py
ps -ef | grep e1_dds_bridge
ps -ef | grep highcontrol
```

## Технические заметки

- helper переведен на `DDSWrapper` из SDK
- debug-лог helper пишет в stderr и виден через `start_e1_server.sh`
- `publish_control` виден в логах
- `status_sample` пока не подтвержден, поэтому state mapping еще не завершен

## Следующие инженерные шаги

1. Добить чтение `Robot_Status_Topic`
2. Добавить safe single-joint arm control через `Motor_Cmd_Topic`
3. Поднять локальный стек Jetson Orin:
   - TTS: `Piper`
   - STT: `faster-whisper`
   - LLM: `Qwen2.5-3B-Instruct` через `llama.cpp`
