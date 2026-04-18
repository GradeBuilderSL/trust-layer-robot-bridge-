# Trust Layer Robot Bridge

HTTP bridge between physical robots and the Trust Layer platform.

Confirmed active robot paths in this repo:
- Noetix N2 via HTTP adapter
- Unitree H1 via `h1_server.py`
- Noetix E1 via `e1_server.py` + native DDS helper
- Mock/sim mode for local development

## Quick Start

### Mock mode

```bash
pip install -r requirements.txt
python -m bridge.main
```

Bridge starts on `http://127.0.0.1:8080`.

### Noetix E1 on Jetson

The E1 path has two processes:
- `bridge/e1_server.py` on `:8083`
- `bridge.main` on `:8080`

Recommended launch order on Jetson:

```bash
# terminal 1
cd /home/noetix/trust-layer-robot-bridge-
E1_TRANSPORT=noetix_dds bash scripts/start_e1_server.sh

# terminal 2
cd /home/noetix/trust-layer-robot-bridge-
ADAPTER_TYPE=e1 ROBOT_URL=http://127.0.0.1:8083 BRIDGE_PORT=8080 python3 -m bridge.main
```

Health checks:

```bash
curl -s http://127.0.0.1:8083/health
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/brain/status
```

Current known-good E1 status:
- `e1_server` answers on `:8083`
- native helper `native/build/e1_dds_bridge` builds and starts
- command publish path into DDS is confirmed
- local speech output through `espeak-ng` is confirmed
- SSH over Wi-Fi is confirmed after disabling client isolation on the router

Current E1 limitation:
- `Robot_Status_Topic` telemetry is not yet confirmed in the helper, so `/api/state` can still show placeholder values while command publish works.

## Repository Structure

```text
bridge/
  main.py                 FastAPI bridge
  e1_server.py            Noetix E1 low-level HTTP server
  e1_adapter.py           Trust Layer adapter for E1
  h1_server.py            Unitree H1 HTTP bridge
native/
  e1_dds_bridge.cpp       Native DDS helper for E1
  CMakeLists.txt          Helper build
scripts/
  start_e1_server.sh      Start E1 low-level server on Jetson
  start_e1_bridge.sh      Start Trust Layer bridge against E1
  e1_dialog.py            Simple text-to-voice dialog loop
docs/
  OPERATOR_E1.md          Live operator notes for E1
  ROBOT_SETUP.md          Install and bring-up notes
  JETSON_LOCAL_AI.md      Speech and local AI notes for Jetson Orin
```

## API Overview

### Bridge `:8080`

- `GET /health`
- `GET /robot/state`
- `POST /robot/move`
- `POST /robot/stop`
- `GET /brain/status`
- `POST /chat`

### E1 server `:8083`

- `GET /health`
- `GET /api/state`
- `POST /api/cmd/vel`
- `POST /api/cmd/stop`
- `POST /api/cmd/gesture`
- `POST /api/audio/speak`

## Environment

### Common bridge vars

| Variable | Default | Description |
|---|---|---|
| `ADAPTER_TYPE` | `mock` | `mock`, `http`, `h1`, `e1` |
| `ROBOT_URL` | adapter-specific | Base URL of robot-facing server |
| `BRIDGE_PORT` | `8080` | Trust Layer bridge port |

### E1 server vars

| Variable | Default | Description |
|---|---|---|
| `E1_SERVER_PORT` | `8083` | E1 low-level server port |
| `E1_TRANSPORT` | `auto` | `noetix_dds`, `ros2`, `sim` |
| `E1_SDK_ROOT` | auto-detect | Noetix SDK root |
| `E1_DDS_CONFIG_PATH` | auto-detect | Path to `dds.xml` |
| `E1_SDK_LIB_DIR` | auto-detect | SDK libs dir |
| `E1_DDS_HELPER_PATH` | auto-detect | Native helper path |
| `IFLYTEK_APP_ID` | empty | Optional iFlytek integration |
| `IFLYTEK_API_KEY` | empty | Optional iFlytek integration |
| `IFLYTEK_API_SECRET` | empty | Optional iFlytek integration |

## E1 Wi-Fi Notes

Wi-Fi setup was one of the main operational issues during bring-up.

What was confirmed:
- Jetson can join Wi-Fi with `nmcli`
- SSH over Wi-Fi works once robot and operator laptop are on the same non-isolated WLAN
- guest networks may give internet access but still block SSH/HTTP between clients

Recommended Jetson commands:

```bash
sudo nmcli dev wifi list
sudo nmcli dev wifi connect "KUPIROBOT" password "Kupirobot"
hostname -I
```

Useful diagnostics:

```bash
ping -c 1 8.8.8.8
sudo systemctl status ssh --no-pager
ss -ltnp | grep :22
```

Operational guidance:
- prefer the main office/home SSID over `Guest`
- if `Guest` must be used, disable client isolation / AP isolation first
- do not remove LAN until SSH and health checks work over Wi-Fi
- after moving to Wi-Fi, verify both `ssh noetix@<wifi-ip>` and `curl http://<wifi-ip>:8083/health`

## Jetson Speech

The fastest confirmed speech path on E1 is local TTS on Jetson, independent of Trust Layer.

Install:

```bash
sudo apt update
sudo apt install -y espeak-ng ffmpeg
```

Verify Russian voice:

```bash
espeak-ng --voices | grep -i ru
espeak-ng -v ru "Привет. Я робот E1. Русский голос работает."
```

Use through `e1_server`:

```bash
curl -s -X POST "http://127.0.0.1:8083/api/audio/speak" \
  -H "Content-Type: application/json" \
  -d '{"text":"Привет. Я робот E1. Русский голос работает.","lang":"ru"}'
```

Expected response:

```json
{"status":"ok","method":"espeak","spoken":"Привет. Я робот E1. Русский голос работает."}
```

See also:
- [docs/JETSON_LOCAL_AI.md](docs/JETSON_LOCAL_AI.md)
- [docs/OPERATOR_E1.md](docs/OPERATOR_E1.md)

## Bringing Trust Layer Online

Once `e1_server` is up:

```bash
cd /home/noetix/trust-layer-robot-bridge-
ADAPTER_TYPE=e1 ROBOT_URL=http://127.0.0.1:8083 BRIDGE_PORT=8080 python3 -m bridge.main
```

Quick checks:

```bash
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/brain/status
curl -s -X POST "http://127.0.0.1:8080/chat" \
  -H "Content-Type: application/json" \
  -d '{"message":"Привет, как тебя зовут?"}'
```

## Notes

- `scripts/e1_dialog.py` can use `:8080/chat` for text replies and `:8083/api/audio/speak` for voice output.
- Local full-offline stack under evaluation on Jetson Orin: `Piper` for TTS, `faster-whisper` for STT, `Qwen2.5-3B-Instruct` via `llama.cpp` for LLM.
