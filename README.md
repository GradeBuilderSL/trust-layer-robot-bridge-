# Trust Layer Robot Bridge

FastAPI bridge between a physical robot and the Trust Layer platform.
Runs on the robot's onboard computer or on a local laptop for testing.

Supports:
- **Noetix N2** (wheeled AMR) — via HTTP adapter
- **Unitree H1** (humanoid) — via HTTP adapter to `h1_server.py`
- **Simulation** — mock adapter with physics simulation, no robot required

---

## Quick Start

### Simulation mode (no robot needed)

```bash
pip install -r requirements.txt
python -m bridge.main
```

Bridge starts at `http://localhost:8080` with the mock adapter.

### Real Noetix N2 robot

```bash
ADAPTER_TYPE=http ROBOT_URL=http://192.168.1.100:8000 python -m bridge.main
```

### Unitree H1 humanoid

```bash
# Start H1 server on the robot first (requires Unitree SDK2)
python bridge/h1_server.py

# Then start the bridge pointing to H1 server
ADAPTER_TYPE=h1 ROBOT_URL=http://192.168.123.1:8081 python -m bridge.main
```

### Docker

```bash
docker build -t trust-layer-bridge .
docker run -p 8080:8080 -e ADAPTER_TYPE=mock trust-layer-bridge
```

---

## Repository Structure

```
bridge/
├── main.py                # FastAPI application — all HTTP endpoints
├── mock_adapter.py        # Physics simulation (mock robot)
├── http_adapter.py        # Noetix N2 HTTP API adapter
├── h1_adapter.py          # Unitree H1 humanoid adapter
├── h1_server.py           # H1 REST bridge (runs on robot with Unitree SDK2)
├── safety_pipeline.py     # Local safety checks (speed, tilt, battery, obstacles)
├── watchdog.py            # Connection watchdog — triggers SAFE_FALLBACK on loss
├── connectivity_monitor.py # Network connectivity monitoring
├── event_buffer.py        # Timestamped event log (reasoning history)
├── local_brain.py         # Onboard LLM / local decision support
├── license_manager.py     # License validation and activation
└── profession_deployer.py # Deploys profession packs to the robot
robots/
├── n2.yaml                # Noetix N2 robot profile
└── h1.yaml                # Unitree H1 robot profile
```

---

## API Reference

### Core

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Bridge health and adapter status |
| `GET` | `/robot/state` | Current telemetry: position, velocity, battery, tilt, sensors |
| `POST` | `/robot/move` | Send velocity command `{vx, vy, wz}` through safety pipeline |
| `POST` | `/robot/stop` | Emergency stop |
| `POST` | `/robot/heartbeat` | Keep-alive from operator UI |

### Capabilities & Safety

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/robot/capabilities` | Hardware capability scan (camera, lidar, IMU, drive, battery…) + ROS2 discovery |
| `GET` | `/robot/reasoning` | Recent safety pipeline decisions (ALLOW / DENY / LIMIT with reason codes) |
| `GET` | `/pipeline/stats` | Safety pipeline statistics |
| `GET` | `/watchdog/status` | Watchdog state and last heartbeat time |

### Scenarios (testing)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/scenario/inject` | Inject test conditions: `{battery_pct, tilt_deg, human_distance_m, obstacle_distance_m}` |
| `POST` | `/scenario/clear` | Reset to normal operating state |

### Perception

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/camera/capture` | Capture frame from robot camera |
| `GET` | `/camera/frame` | Latest camera frame (JPEG) |
| `GET` | `/camera/status` | Camera availability and settings |

### Voice

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/voice/speak` | Send TTS command `{text, lang}` to robot speaker |
| `GET` | `/voice/listen` | Activate STT and return recognized text |

### Brain (onboard LLM)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/brain/status` | Local LLM status |
| `POST` | `/brain/sync` | Sync knowledge base from workstation |
| `POST` | `/chat` | Send chat message to local brain |
| `POST` | `/chat/send` | Alias for `/chat` |

### License

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/license/status` | Current license state |
| `GET` | `/license/activation-request` | Get activation request token |
| `POST` | `/license/activate` | Activate with license key |
| `POST` | `/license/apply-token` | Apply signed activation token |

---

## Safety Pipeline

Every move command passes through local checks before reaching the robot:

| # | Check | Condition | Action | Reason Code |
|---|-------|-----------|--------|-------------|
| 1 | Battery | < 10% | DENY | `BATT-001` |
| 2 | Tilt | > 20° | DENY | `TILT-001` |
| 3 | Human proximity | < 1.5 m | DENY | `HUMAN-001` |
| 4 | Human proximity | < 2.5 m | LIMIT speed to 0.1 m/s | `HUMAN-002` |
| 5 | Speed | > 0.8 m/s | CLAMP | `SPEED-001` |
| 6 | Angular velocity | > 1.0 rad/s | CLAMP | `ANG-001` |
| 7 | Obstacle | < 0.5 m | DENY | `OBS-001` |

The pipeline is synchronous and deterministic — no ML, no network calls.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ADAPTER_TYPE` | `mock` | Adapter: `mock`, `http`, or `h1` |
| `ROBOT_URL` | `http://192.168.1.100:8000` | Robot API base URL (N2: port 8000, H1: port 8081) |
| `BRIDGE_PORT` | `8080` | Port to listen on |
| `POLL_HZ` | `10` | Telemetry poll frequency (Hz) |
| `WORKSTATION_URL` | `http://localhost:8888` | Trust Layer platform URL for sync |
| `DATA_DIR` | `/data` | Data directory for logs and knowledge base |
| `DECISION_LOG_URL` | _(empty)_ | Optional: URL of Decision Log service |
| `ACTIVATION_SERVER` | _(built-in)_ | License activation server URL |

---

## Connecting to Trust Layer Platform

The bridge is called by the Operator UI (`operator_ui` service).
Set the bridge URL in the platform:

```bash
# In trust-layer platform docker-compose or .env
FLEET_BRIDGE_URLS=http://<robot-ip>:8080
```

Connection flow:
```
Operator UI (port 8893)
    │
    ├── GET /api/fleet          → polls /robot/state
    ├── POST /api/fleet/0/move  → /robot/move (through safety pipeline)
    ├── GET /api/fleet/0/capabilities → /robot/capabilities (+ ROS2 discovery)
    └── POST /api/chat          → LLM chat with capability-aware responses
```

---

## ROS2 Discovery

When `ros2` is available on the robot, `/robot/capabilities` automatically runs:
- `ros2 topic list -t` — discovers active topics
- `ros2 node list` — discovers running nodes

Topics are mapped to capability keys: camera, lidar, IMU, odometry, navigation, mapping, etc.
Results appear in the `ros_discovery` field of the capabilities response.

---

## License

Trust Layer Robot Bridge is part of the Trust Layer platform.
