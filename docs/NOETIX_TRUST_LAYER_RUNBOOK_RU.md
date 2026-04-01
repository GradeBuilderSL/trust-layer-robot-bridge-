# Noetix + Trust Layer Robot Bridge: полный конспект (runbook)

Документ собирает пошаговый путь развёртывания и типичные проблемы: робот **Noetix** (Ubuntu, пример адреса `192.168.55.102`), **Trust Layer Robot Bridge** в Docker, связка с **Trust Layer** на ПК (`trust-layer`), обновление кода с ПК без GitHub на роботе.

---

## 1. Роли и адреса

| Компонент | Где живёт | Пример |
|-----------|-----------|--------|
| OEM HTTP API робота | на роботе | `http://127.0.0.1:8080` (порт может отличаться) |
| **Trust Layer Robot Bridge** | Docker на роботе, `--network host` | снаружи: `http://192.168.55.102:8090` |
| Trust Layer (UI, Ollama, edge) | ПК, `docker compose` в `trust-layer` | переменные `.env`: `ROBOT_BRIDGE_URL`, `FLEET_BRIDGE_URLS` → URL бриджа на роботе |

Важно: **`ROBOT_URL` в `.env` бриджа — это не порт бриджа (8090), а базовый URL заводского API**, к которому ходит адаптер `http` (например `http://127.0.0.1:8080`).

---

## 2. Репозитории и пути

- **Бридж (робот):** `trust-layer-robot-bridge` — на роботе обычно `~/trust-layer-robot-bridge`.
- **Платформа (ПК):** `trust-layer` — `docker-compose.yml`, `.env` с `ROBOT_BRIDGE_URL=http://192.168.55.102:8090` (или актуальный IP/порт).

---

## 3. Первичное развёртывание бриджа на роботе

### 3.1. Код без GitHub на роботе

Если на роботе нет DNS/доступа к GitHub:

1. Обновить папку проекта с ПК (scp, rsync, общая папка).
2. На роботе запускать скрипт обновления с **`SKIP_GIT_PULL=1`**, иначе `git pull` упадёт.

```bash
cd ~/trust-layer-robot-bridge
SKIP_GIT_PULL=1 bash update_bridge.sh
```

Скрипт **обязательно должен быть актуальной версии с ПК** — иначе переменная `SKIP_GIT_PULL` может отсутствовать.

### 3.2. Сборка Docker при слабом DNS

- В `Dockerfile` бриджа могут использоваться **`HTTP_PROXY` / `HTTPS_PROXY`** для `pip` при сборке.
- Вариант с каталогом **`wheels/`** и `pip install --no-index` — колёса заранее качаются на ПК (например `scripts/download_wheels.ps1` под **linux/arm64**).

### 3.3. Что делает `update_bridge.sh`

1. (Опционально) `git pull`
2. `docker build -t trust-bridge .`
3. `docker stop` / `docker rm` контейнера `trust-bridge`
4. `docker run` с **`--network host`**, переменными из `.env` и монтированием `./libs:/app/libs:ro`

Имя контейнера: **`trust-bridge`**.

---

## 4. Файл `.env` на роботе (`~/trust-layer-robot-bridge/.env`)

Минимальный пример (порты типичные для обсуждаемой схемы):

```bash
ADAPTER_TYPE=http
ROBOT_URL=http://127.0.0.1:8080
BRIDGE_PORT=8090
```

- **`BRIDGE_PORT`** — порт, на котором слушает **сам бридж** (FastAPI).
- **`ROBOT_URL`** — база **OEM API** (там должны быть пути вроде `/api/status` для Noetix).

### 4.1. Авторизация OEM API (401 Unauthorized)

Если `curl http://127.0.0.1:8080/api/status` даёт **`401` и `{"error":"Unauthorized"}`**, бридж без заголовков не сможет выставить `connected: true`.

В коде бриджа поддерживаются переменные окружения (и пробрасываются из `update_bridge.sh` в контейнер):

| Переменная | Смысл |
|------------|--------|
| `ROBOT_HTTP_AUTHORIZATION` | Полное значение заголовка `Authorization` (как в документации OEM) |
| `ROBOT_BEARER_TOKEN` | Упрощение: отправляется как `Authorization: Bearer <токен>` |
| `ROBOT_API_KEY` | Заголовок `X-API-Key` |

Проверка с робота (подставить реальный заголовок из доки Noetix):

```bash
curl -sS -H "Authorization: Bearer ВАШ_ТОКЕН" http://127.0.0.1:8080/api/status | head -c 400
```

После правок `.env` контейнер нужно **пересоздать** (см. раздел 6).

### 4.2. Окончания строк (CRLF) с Windows

Если `.env` редактировали на Windows и копировали на робот, возможны **`\r` в конце строк** — тогда `source .env` портит значения.

```bash
sed -i 's/\r$//' ~/trust-layer-robot-bridge/.env
```

### 4.3. `localhost` vs `127.0.0.1`

При **`--network host`** у контейнера бриджа `localhost` на роботе обычно эквивалентен `127.0.0.1`. Для надёжности часто пишут **`127.0.0.1`**.

---

## 5. Диагностика: порты, API, `connected`

### 5.1. «Нет контейнера `trust-bridge`»

Сообщение `No such container: trust-bridge` значит контейнер ещё не создавали или удалили. Не ошибка — просто запустить:

```bash
cd ~/trust-layer-robot-bridge
SKIP_GIT_PULL=1 bash update_bridge.sh
```

### 5.2. Мусорные контейнеры в `docker ps -a`

Имена вроде **`musing_cannon`** с командой `pip ins…` и статусом **Exited** — часто артефакты прерванной сборки; на бридж не влияют. При желании: `docker rm <id_or_name>`.

### 5.3. Что слушает робот

```bash
ss -tlnp
```

Искать порты OEM (например **8080**), бриджа (**8090**), SSH (**22**). Порт **8000** может быть пустым — тогда **`Connection refused`** на `http://127.0.0.1:8000` ожидаем; **`ROBOT_URL` нужно менять** на реальный порт API (например 8080).

### 5.4. Поле `connected` в `/health` бриджа

- **`/health` с ПК по `http://192.168.55.102:8090`** отвечает «бридж жив».
- **`"connected": false`** означает: адаптер `http` **не получает валидный статус** с `ROBOT_URL` (часто `GET …/api/status`: нет сервиса, 401 без токена, не тот JSON).

Проверки **на роботе**:

```bash
curl -sS -o /tmp/st.txt -w "http_code=%{http_code}\n" "http://127.0.0.1:8080/api/status"
head -c 400 /tmp/st.txt
```

Из контейнера при `network host` обычно то же самое, что с хоста:

```bash
docker exec trust-bridge curl -sS --max-time 2 "http://127.0.0.1:8080/api/status" | head -c 400
```

### 5.5. PowerShell на ПК

В PowerShell **`curl`** часто алиас на **`Invoke-WebRequest`**. Для HTTP-CLI использовать:

```powershell
curl.exe -s http://192.168.55.102:8090/health
```

---

## 6. Когда пересобирать образ, когда только пересоздать контейнер

| Изменение | Действие на роботе |
|-----------|-------------------|
| Только **`.env`** (URL, порт, токены) | Достаточно **`docker rm -f trust-bridge`** и снова **`SKIP_GIT_PULL=1 bash update_bridge.sh`** (или ручной `docker run` как в скрипте). **`docker build` не обязателен**, но `update_bridge.sh` всё равно вызовет build — обычно быстро из кэша. |
| **Код Python** бриджа (`bridge/*.py` и т.д.) | Нужен **`docker build`** (образ копирует код внутрь; монтируется в основном только **`libs`**). |

---

## 7. Обновление бриджа с ПК (типовой цикл)

1. На ПК: актуальный код в `trust-layer-robot-bridge`.
2. Скопировать на робот каталог (или нужные файлы), например:
   - `bridge/http_adapter.py`, `update_bridge.sh`, при необходимости `Dockerfile`, `wheels/`, …
3. На роботе:
   ```bash
   cd ~/trust-layer-robot-bridge
   sed -i 's/\r$//' update_bridge.sh  # если копировали с Windows
   docker rm -f trust-bridge 2>/dev/null || true
   SKIP_GIT_PULL=1 bash update_bridge.sh
   ```
4. Проверка:
   ```bash
   docker ps
   curl -s http://127.0.0.1:8090/health
   ```

---

## 8. Trust Layer на ПК

В **`trust-layer/.env`** указать URL бриджа на роботе, например:

```bash
ROBOT_BRIDGE_URL=http://192.168.55.102:8090
# при флоте:
FLEET_BRIDGE_URLS=http://192.168.55.102:8090
```

После смены `.env` перезапустить соответствующие сервисы `docker compose`.

Отдельно: на ПК для локальных тестов может крутиться свой контейнер **`trust-bridge`** (например **mock**, порт **8000→8080**) — это **не** робот; путать с бриджем на Noetix не стоит.

---

## 9. Логика адаптера `http` (кратко)

- Поддерживаются стили **Isaac Sim** (`/robot/state` и т.д.) и **Noetix N2** (`/api/status`, `/api/cmd/velocity`, …).
- Автоопределение API; при неясности по умолчанию ориентир на **Noetix N2**.
- Ответы только с **`error`**, без полей состояния, **не** считаются валидным state — чтобы не помечать подключение как успешное при 401/ошибках.

Подробности — в `bridge/http_adapter.py`.

---

## 10. Мелочи из практики

- **nano:** выход — **`Ctrl+X`**, затем подтверждение сохранения (**Y/N**), имя файла — **Enter**.
- **SSH с машины ассистента** к роботу может быть недоступен (**Permission denied**) — команды выполняют локально под `noetix`.
- Порт **8080** на роботе ранее фигурировал как OEM с **Unauthorized** — это нормальное поведение без токена; бридж после добавления переменных авторизации и правильного **`ROBOT_URL`** должен получать **200** и JSON статуса.

---

## 11. Быстрый чеклист «всё работает»

- [ ] `ss -tlnp`: OEM API слушает ожидаемый порт; бридж — **8090** (или ваш `BRIDGE_PORT`).
- [ ] `curl` к **`/api/status`** с нужными заголовками даёт **200** и JSON с полями статуса (не только `Unauthorized`).
- [ ] `curl http://127.0.0.1:8090/health` на роботе: **`"status":"ok"`**, **`"connected":true`** (после успешного опроса API).
- [ ] С ПК: `curl.exe http://<IP_робота>:8090/health` — **200**.
- [ ] В `trust-layer/.env` на ПК указан тот же URL бриджа.

---

*Документ отражает пройденный сценарий: Noetix, Docker bridge, связка с Trust Layer на Windows, обход отсутствия GitHub/DNS на роботе, разбор портов 8000/8080/8090 и авторизации OEM API.*
