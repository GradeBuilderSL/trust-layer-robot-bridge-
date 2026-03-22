#!/bin/bash
# Trust Layer Robot Bridge — обновление
# Запускать на роботе через SSH:
#   ssh noetix@192.168.1.100
#   cd trust-layer-robot-bridge
#   bash update_bridge.sh
#
# Что делает: pull новый код, пересобрать Docker, перезапустить

set -e

echo "═══════════════════════════════════════════════"
echo "  Trust Layer Robot Bridge — обновление"
echo "═══════════════════════════════════════════════"
echo ""

# Check we're in the right directory
if [ ! -f "bridge/main.py" ]; then
    echo "ОШИБКА: запустите из папки trust-layer-robot-bridge"
    echo "  cd ~/trust-layer-robot-bridge && bash update_bridge.sh"
    exit 1
fi

# Pull latest code
echo "1/4  Загрузка обновлений из Git..."
git pull origin main --ff-only || {
    echo "ОШИБКА: не удалось обновить. Возможно есть локальные изменения."
    echo "  Выполните: git stash && git pull origin main && git stash pop"
    exit 1
}
echo "     ✓ Код обновлён"
echo ""

# Rebuild Docker image
echo "2/4  Сборка Docker образа (может занять 1-2 минуты)..."
docker build -t trust-bridge . --quiet || {
    echo "ОШИБКА: сборка не удалась. Проверьте Docker."
    exit 1
}
echo "     ✓ Образ собран"
echo ""

# Stop old container
echo "3/4  Остановка старого bridge..."
docker stop trust-bridge 2>/dev/null || true
docker rm trust-bridge 2>/dev/null || true
echo "     ✓ Старый контейнер удалён"
echo ""

# Start new container
echo "4/4  Запуск нового bridge..."

# Read config from .env if exists
ADAPTER=${ADAPTER_TYPE:-http}
ROBOT_URL_VAR=${ROBOT_URL:-http://127.0.0.1:8000}
BRIDGE_PORT=${BRIDGE_PORT:-8080}
WORKSTATION=${WORKSTATION_URL:-}

if [ -f ".env" ]; then
    source .env 2>/dev/null || true
    ADAPTER=${ADAPTER_TYPE:-$ADAPTER}
    ROBOT_URL_VAR=${ROBOT_URL:-$ROBOT_URL_VAR}
    BRIDGE_PORT=${BRIDGE_PORT:-$BRIDGE_PORT}
    WORKSTATION=${WORKSTATION_URL:-$WORKSTATION}
fi

docker run -d \
    --name trust-bridge \
    --restart unless-stopped \
    --network host \
    -e ADAPTER_TYPE="${ADAPTER}" \
    -e ROBOT_URL="${ROBOT_URL_VAR}" \
    -e BRIDGE_PORT="${BRIDGE_PORT}" \
    -e WORKSTATION_URL="${WORKSTATION}" \
    -e DECISION_LOG_URL="${DECISION_LOG_URL:-}" \
    -e TRUST_LAYER_LIBS=/app/libs \
    -e WATCHDOG_TIMEOUT_MS="${WATCHDOG_TIMEOUT_MS:-30000}" \
    -e DISCONNECTED_BEHAVIOR="${DISCONNECTED_BEHAVIOR:-return_base}" \
    -e BASE_POSITION="${BASE_POSITION:-}" \
    -v "$(pwd)/libs:/app/libs:ro" \
    trust-bridge

echo "     ✓ Bridge запущен"
echo ""

# Health check
echo "Проверка здоровья..."
sleep 3
HEALTH=$(curl -s http://127.0.0.1:${BRIDGE_PORT}/health 2>/dev/null || echo '{"status":"error"}')
STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','error'))" 2>/dev/null || echo "error")

if [ "$STATUS" = "ok" ]; then
    echo ""
    echo "═══════════════════════════════════════════════"
    echo "  ✓ Bridge обновлён и работает!"
    echo "  Адрес: http://$(hostname -I | awk '{print $1}'):${BRIDGE_PORT}"
    echo "  Адаптер: ${ADAPTER}"
    echo "═══════════════════════════════════════════════"
else
    echo ""
    echo "⚠ Bridge запущен, но health check не прошёл."
    echo "  Проверьте логи: docker logs trust-bridge --tail 20"
fi
