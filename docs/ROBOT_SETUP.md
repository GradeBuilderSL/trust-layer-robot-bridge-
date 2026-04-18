# Установка Trust Layer Bridge на робота

Базовая установка для N2 и актуальные заметки по E1 Jetson.

## Требования

- Python 3.8+
- SSH-доступ к onboard компьютеру
- сеть между роботом и операторским ноутбуком

## Базовая установка

### 1. Подключиться к роботу

```bash
ssh noetix@192.168.1.100
```

### 2. Склонировать репозиторий

```bash
cd /home/noetix
git clone <REPO_URL> trust-layer-robot-bridge-
cd trust-layer-robot-bridge-
```

### 3. Установить зависимости Python

```bash
python3 -m pip install -r requirements.txt
```

### 4. Запустить bridge

```bash
ADAPTER_TYPE=http python3 -m bridge.main
```

### 5. Проверить

```bash
curl http://127.0.0.1:8080/health
```

## Noetix E1: Jetson bring-up

Для E1 используются два процесса:

- `bridge/e1_server.py` на `:8083`
- `bridge.main` на `:8080`

Рабочая последовательность:

```bash
# terminal 1
cd /home/noetix/trust-layer-robot-bridge-
E1_TRANSPORT=noetix_dds bash scripts/start_e1_server.sh

# terminal 2
cd /home/noetix/trust-layer-robot-bridge-
ADAPTER_TYPE=e1 ROBOT_URL=http://127.0.0.1:8083 BRIDGE_PORT=8080 python3 -m bridge.main
```

Проверка:

```bash
curl -s http://127.0.0.1:8083/health
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/brain/status
```

## Wi-Fi и SSH на Jetson

Подтвержденный рабочий сценарий:

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

Нюансы:

- guest Wi-Fi может давать интернет, но блокировать SSH/HTTP между клиентами
- если используется guest-сеть, отключить client isolation / AP isolation на роутере
- не отключать LAN, пока Wi-Fi SSH и `curl http://<wifi-ip>:8083/health` не подтверждены

## Речь на Jetson

Минимальный подтвержденный стек:

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

Подробности по локальной речи и будущему локальному AI стеку:

- [JETSON_LOCAL_AI.md](JETSON_LOCAL_AI.md)
- [OPERATOR_E1.md](OPERATOR_E1.md)
