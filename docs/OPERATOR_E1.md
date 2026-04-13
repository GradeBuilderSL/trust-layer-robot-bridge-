# Подключение Noetix E1 к Trust Layer

Это инструкция оператору. Цель — за 15 минут получить рабочую связку:
**Operator UI → Trust Layer Bridge → e1_server (на роботе) → железо E1**.

> ⚠ **Перед началом.** Никогда не подключайтесь по SSH к плате управления
> движением (RK3588S) во время работы — это убьёт цикл EtherCAT, и робот
> упадёт. Все наши скрипты работают только с **AI-платой Jetson Orin Nano
> Super** (`192.168.55.101`), не с RK3588S.

## TL;DR — три команды до ходячего робота

```bash
# 1. На Jetson E1 (ssh noetix@192.168.55.101)
E1_TRANSPORT=ros2 bash /opt/trust-layer-bridge/scripts/start_e1_server.sh

# 2. На ноутбуке оператора
bash ~/trust-layer-robot-bridge/scripts/start_e1_bridge.sh

# 3. Pre-flight чек ПЕРЕД тем как звать оператора
bash ~/trust-layer-robot-bridge/scripts/test_e1.sh
```

Если `test_e1.sh` показывает `PASS: 7 checks green` — открываем
`http://localhost:8893`, вкладка **Fleet**, робот виден как `Noetix E1`.
Пишем в чат «иди вперёд», «развернись», «помаши рукой» — робот выполняет.

Полные детали — ниже.

---

## 0. Что мы имеем

| Компонент | Где живёт | Что делает |
|---|---|---|
| **e1_server.py** | На роботе, Jetson Orin Nano Super | REST-обёртка, говорит с Noetix DDS / ROS 2 / sim |
| **E1Adapter** | В bridge на ноутбуке оператора | Стандартный Trust Layer adapter, ходит к e1_server по HTTP |
| **bridge (main.py)** | Ноутбук оператора | Safety pipeline + REST для UI |
| **operator_ui** | Ноутбук/сервер | Веб-интерфейс, видит робота в Fleet |

---

## 1. Подготовка робота (один раз)

### 1.1 Распаковать и поднять

1. Достать E1 из коробки. Поставить на ноги либо повесить на раму.
2. **Вставить батарею** (кнопкой вверх, индикатором вниз — см. delivery doc).
3. Подключить USB-приёмник геймпада в любой USB на спине.
4. Короткое нажатие на кнопку питания → потом длинное → дождаться "beep" и
   полной заливки светодиодов.
5. Через ~10 секунд робот сам войдёт в **Enabled mode** (джойнты задемпфированы).
6. Поставить робота вертикально, нажать `LB + −` → **Preparation mode**.
7. Если ноги слегка согнуты — `LB + X` → **Walking mode**. Робот балансирует сам.

### 1.2 Сеть

E1 поднимает сеть `192.168.55.0/24`. Подключите ноутбук в ту же подсеть
(Wi-Fi точка робота или Ethernet). Проверьте:

```bash
ping -c 2 192.168.55.101
```

### 1.3 Установить bridge на Jetson E1 (один раз)

```bash
ssh noetix@192.168.55.101         # пароль: noetix
sudo mkdir -p /opt/trust-layer-bridge && sudo chown $USER /opt/trust-layer-bridge
git clone https://github.com/GradeBuilderSL/trust-layer-robot-bridge- /opt/trust-layer-bridge
cd /opt/trust-layer-bridge
pip3 install -r requirements.txt

# Опциональные транспорты:
pip3 install cyclonedds                            # для noetix_dds
# ROS 2 уже установлен на образе Jetson — sourcing делает start_e1_server.sh

# Распаковать SDK от Noetix (вы скачаете её отдельно из их репозитория):
tar xzf dds_demo_release_e1.tar.gz -C /opt/noetix-sdk/
# Дальше — см. раздел 4 ниже про DDS-топики.
```

---

## 2. Запустить связку

### 2.1 На роботе (терминал A — внутри SSH сессии в Jetson):

```bash
cd /opt/trust-layer-bridge
bash scripts/start_e1_server.sh
```

По умолчанию `E1_TRANSPORT=auto`: попытается DDS → ROS 2 → sim.
Чтобы явно зафиксировать ROS 2:

```bash
E1_TRANSPORT=ros2 bash scripts/start_e1_server.sh
```

В логе должно появиться:

```
e1_server INFO E1 transport: ros2
e1_server INFO E1 server [ros2] listening on :8083
```

Smoke-тест с того же Jetson:

```bash
curl -s http://127.0.0.1:8083/health
curl -s http://127.0.0.1:8083/api/state | jq .
```

### 2.2 На ноутбуке оператора (терминал B):

```bash
cd ~/trust-layer-robot-bridge
bash scripts/start_e1_bridge.sh
```

Скрипт сам пингует `e1_server` перед стартом и предупредит, если он не отвечает.
Bridge стартует на `:8080`. Проверка:

```bash
curl -s http://127.0.0.1:8080/health | jq .
# должно быть: "adapter": "e1", "connected": true
```

### 2.3 Operator UI

UI читает робота через `_poll_bridge` в `services/operator_ui/main.py`.
Убедитесь, что в `FLEET_BRIDGE_URLS` (env var) указан адрес вашего bridge,
например:

```bash
export FLEET_BRIDGE_URLS=http://127.0.0.1:8080
python -m services.operator_ui.main
```

Откройте http://localhost:8893 → вкладка **Fleet**. Должен появиться робот:
- **Name:** Noetix E1
- **Model:** Noetix E1
- **Status:** online

### 2.4 Pre-flight чек одной командой

Перед тем как звать оператора, прогоните `test_e1.sh`. Он проверяет всю
цепочку `e1_server → bridge → NL gateway → episode capture` и ругается
на каждый уровень, где что-то молча сломалось:

```bash
cd ~/trust-layer-robot-bridge
bash scripts/test_e1.sh
# 7 проверок: e1_server /health, /api/state, bridge /health,
#             /robot/move через gate, /robot/stop, NL chat,
#             episode capture
```

Значения по умолчанию указывают на `192.168.55.101:8083`. Если Jetson
на другом IP — задайте явно:

```bash
E1_SERVER_URL=http://10.0.0.7:8083 \
  BRIDGE_URL=http://127.0.0.1:8080 \
  bash scripts/test_e1.sh
```

Один раз красный — оператора не пускать, идти в логи.

---

## 3. Управление

### 3.1 Из Operator UI

- **Chat tab** — пишите по-русски: «иди вперёд», «остановись», «помаши рукой»,
  «развернись». NL-команда уходит в `nl_command_gateway`, тот — в bridge,
  тот — в адаптер E1.
- **Map tab** — клик по точке = `navigate_to`. Адаптер E1 пока не реализует
  `navigate_to` напрямую (lowcontrol mode у Noetix закрыт), используется
  `LocalNavigator` (прямая езда + safety stop).

### 3.2 Из API напрямую

```bash
# Перейти в walking mode (если робот в preparation/enabled):
curl -X POST http://127.0.0.1:8080/robot/action \
  -H 'Content-Type: application/json' \
  -d '{"action_type":"mode","params":{"mode":"walking"}}'

# Поехать вперёд 0.3 м/с:
curl -X POST http://127.0.0.1:8080/robot/move \
  -H 'Content-Type: application/json' \
  -d '{"vx":0.3,"vy":0.0,"wz":0.0}'

# Развернуться:
curl -X POST http://127.0.0.1:8080/robot/move \
  -H 'Content-Type: application/json' \
  -d '{"vx":0,"vy":0,"wz":0.6}'

# Стоп:
curl -X POST http://127.0.0.1:8080/robot/stop

# Жест "поприветствовать":
curl -X POST http://127.0.0.1:8080/robot/action \
  -H 'Content-Type: application/json' \
  -d '{"action_type":"greet"}'

# TTS на iFlytek:
curl -X POST http://127.0.0.1:8080/voice/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"Привет, я Noetix E1","language":"ru"}'
```

Все команды проходят через safety pipeline — если правило срежет, в ответе
будет `gate.decision: DENY` с указанием rule_id.

---

## 3.3 Что нового оператор увидит в чате (обновление 2026-04-13)

Три поведения, с которыми в предыдущих версиях он не сталкивался:

1. **Честная дистанция.** Если операт написал «иди вперёд» и робот
   физически не сдвинулся (например, он в preparation mode или
   e1_server в sim-транспорте), чат скажет:
   > _«Команда отправлена (forward, 2.4м), но робот не сдвинулся
   > (Δ=0.0м). Проверь, что транспорт поддерживает ходьбу.»_

   Раньше чат всегда рапортовал `Прошёл 2.4м` вне зависимости от того,
   сдвинулся ли робот. Теперь `task_executor` читает `/odom` до и после
   команды и честно показывает реальную дельту.

2. **HITL при низкой уверенности.** Если LLM распарсил интент с
   confidence ниже `HITL_CONFIDENCE_THRESHOLD` (по умолчанию 0.6) и это
   не вопрос типа «где туалет?», команда уйдёт в очередь одобрения
   вместо моментального исполнения. Ждёт до `HITL_APPROVAL_TIMEOUT_S`
   секунд (по умолчанию 10). На таймаут → DENY (fail-safe).

   Оператор это увидит как паузу + сообщение _«Оператор не одобрил
   команду. Переформулируй, пожалуйста.»_ если никто не нажал «approve»
   в `/api/approvals` в UI.

3. **Episode capture.** Каждая выполненная команда и каждое решение
   gate пишется в SQLite (`/tmp/trustlayer/episodes/episodes.db`)
   одним атомарным rowoм. Оператор эти записи не видит напрямую, но
   они доступны через:

   ```bash
   curl -s http://localhost:8894/v1/episodes/stats | jq .
   curl -s http://localhost:8894/v1/episodes/export > today.jsonl
   ```

   Это даёт клиенту готовый post-mortem корпус после каждой сессии
   без дополнительной настройки.

---

## 4. Транспорты — что выбрать

| Транспорт | Когда | Что нужно |
|---|---|---|
| `noetix_dds` | Production, прямой доступ к моторам через DDS | `cyclonedds` + распакованная `dds_demo_release_e1.tar.gz` + патчи в `_NoetixDDSTransport` (см. ниже) |
| `ros2` | Dev и любые ROS 2 фреймворки. **Рекомендуем для старта.** | `source /opt/ros/humble/setup.bash` (или jazzy) + ROS 2 нода Noetix, публикующая `/odom`, `/battery_state` и принимающая `/cmd_vel` |
| `sim` | Без железа, проверить пайплайн | Ничего |

### 4.1 Доводка `noetix_dds` (когда у вас будет SDK на руках)

`bridge/e1_server.py`, класс `_NoetixDDSTransport`. Четыре места помечены
`# >>> SDK <<<`. Что вписать:

1. **Импорты** — заменить на то, что лежит в `dds_demo_release_e1.tar.gz`
   (обычно `noetix_e1_sdk.core.channel`, `noetix_e1_sdk.idl.SportCmd_` и т.п.).
2. **Publisher на cmd_vel / SportCmd** — топик имени префикс из `E1_DDS_TOPIC_PREFIX`
   (по умолчанию `rt`, как в Unitree).
3. **Subscriber на lowstate** — fill `self._lowstate` из колбэка с полями
   `pos_x, pos_y, yaw_rad, vx, battery_pct, motor_temp_c`.
4. **`send_velocity`** — собрать SportCmd_ и `self._cmd_pub.write(cmd)`.

После патча тестировать:

```bash
E1_TRANSPORT=noetix_dds bash scripts/start_e1_server.sh
curl -s http://127.0.0.1:8083/api/state | jq .transport   # → "noetix_dds"
```

### 4.2 Запуск на ROS 2 (рекомендуемо для первого подключения)

На роботе должна крутиться ROS 2 нода Noetix, которая:
- принимает `geometry_msgs/Twist` на `/cmd_vel`
- публикует `nav_msgs/Odometry` на `/odom`
- публикует `sensor_msgs/BatteryState` на `/battery_state`

Если её нет — Noetix даёт демо-ноду в `dds_demo_release_e1` (обычно `e1_ros_bridge`).
Запустите её, потом `start_e1_server.sh` подхватит автоматически.

Проверка из соседнего терминала:

```bash
ros2 topic list                  # должны быть /cmd_vel, /odom, /battery_state
ros2 topic echo /odom --once     # видим позицию
```

---

## 5. Голос (iFlytek)

Голосовой модуль E1 работает автономно: wake word **"小顽童"**, потом
вопросы уходят в iFlytek Spark 4.0 Ultra. Trust Layer **не вмешивается**
в этот контур — он чисто пользовательский.

Если хотите управлять TTS из bridge (через `/voice/speak`), поднимите на
Jetson переменные среды:

```bash
export IFLYTEK_APP_ID=...
export IFLYTEK_API_KEY=...
export IFLYTEK_API_SECRET=...
bash scripts/start_e1_server.sh
```

Без креденшалов сервер тихо упадёт на `espeak-ng` или просто залогирует текст.

---

## 6. Завершение работы

1. UI: жмём **Stop** или ctrl-C bridge.
2. На роботе: `LB + −` → preparation mode (поддерживайте робота руками).
3. Поднимите/положите → нажмите `+` → disabled mode.
4. Long-press на батарее → выключение → вынуть батарею.

---

## 7. Траблшутинг

| Симптом | Что проверить |
|---|---|
| `bridge` пишет `connected: false` | `curl http://192.168.55.101:8083/health` с ноутбука. Если 0 — сеть/Jetson лежит. Если 200 — watchdog выдавил; проверь `WATCHDOG_TIMEOUT_MS` в `start_e1_bridge.sh`. |
| Чат говорит _«робот не сдвинулся (Δ=0.0м)»_ | `test_e1.sh` валит на [6/7]. Проверь (а) `mode_e1` в `/api/state` = `walking`, (б) `/odom` реально тикает в `ros2 topic echo`, (в) transport не `sim`. |
| Чат виснет на 10 с, потом _«Оператор не одобрил команду»_ | Низкая confidence у LLM, команда ушла в HITL. Либо переформулируй команду яснее, либо одобри её в `/api/approvals` в UI, либо подкрути `HITL_CONFIDENCE_THRESHOLD`. |
| `e1_server` стартовал, но `transport: sim` | `E1_TRANSPORT=auto` не нашёл ни DDS, ни ROS 2. Проверь `pip show cyclonedds` и `ros2 topic list`. Если видно `/cmd_vel` + `/odom` — запусти `E1_TRANSPORT=ros2 bash scripts/start_e1_server.sh`. |
| Чат говорит OK, команду отправили, робот дёрнулся на миг и встал | `_ROS2Transport` repeater в `e1_server.py` не запустился. Проверь лог: должна быть строка `ROS2 /cmd_vel repeater running at 20 Hz`. Без repeater-а робот останавливается как только пакет не прилетел за 200-500 мс. |
| `mode_e1: enabled`, команды walk → `denied` | Робот в Enabled mode. Перевести в Walking: `curl -X POST .../robot/action -d '{"action_type":"mode","params":{"mode":"walking"}}'` или `LB+X` на геймпаде. |
| `gate.decision: DENY rule_id: WATCHDOG-FALLBACK` | UI/bridge не шлют heartbeat. Проверьте, что bridge на ноутбуке жив. |
| Робот падает при попытке движения | Mode == running без свободного места. Переключите в walking. |
| Перегрев моторов после 5–10 минут | Это особенность E1 (local air cooling). Дайте 2–3 минуты остыть. |
| Не пингуется `192.168.55.101` | Робот не на той сети. Проверьте Wi-Fi/Ethernet. |
| Fleet в UI показывает `Robot-1` вместо `Noetix E1` | ENV-переменные `ROBOT_NAME/ID/MODEL` не пробрасываются в docker robot_bridge. Для docker-сценария убедитесь, что compose-файл их форвардит (в `deployments/live/docker-compose.live.yml` это уже сделано). Для standalone bridge `start_e1_bridge.sh` — они уже заданы через export. |

Для глубокого дебага:

```bash
# Логи bridge (на ноутбуке):
tail -F ~/trust-layer-robot-bridge/bridge.log

# Логи e1_server (на Jetson):
ssh noetix@192.168.55.101 'journalctl -u e1-server -f'   # если как systemd
# или просто оставить терминал A открытым.
```

---

## 8. Контакт с Noetix

- FAE: **Tang Ziyang** — `ziyangtang@noetixrobotics.com`
- Сайт: https://noetixrobotics.com/
- SDK-документация: https://noetixrobotics.feishu.cn/docx/BdJddzSwqoDhd9xixobcXj9XnAe (требует логин)
