#!/usr/bin/env bash
# test_e1.sh — end-to-end smoke test for the Noetix E1 integration.
#
# Run AFTER:
#   1. e1_server.py is running on the Jetson (192.168.55.101:8083).
#   2. bridge/main.py is running on the operator laptop (127.0.0.1:8080,
#      ADAPTER_TYPE=e1, ROBOT_URL pointing at the Jetson).
#   3. (optional) nl_command_gateway :8894 and operator_ui :8893 are up
#      in docker for chat / UI tests.
#
# The script walks the full bridge → app path and fails loudly if any
# layer is silent-broken. Use it as the pre-flight check before letting
# an operator anywhere near the robot.
#
# Usage:
#   bash scripts/test_e1.sh                        # default IPs
#   E1_SERVER_URL=http://10.0.0.7:8083 \
#     BRIDGE_URL=http://127.0.0.1:8080 \
#     NLGW_URL=http://127.0.0.1:8894 \
#     bash scripts/test_e1.sh
set +e

E1_SERVER_URL="${E1_SERVER_URL:-http://192.168.55.101:8083}"
BRIDGE_URL="${BRIDGE_URL:-http://127.0.0.1:8080}"
NLGW_URL="${NLGW_URL:-http://127.0.0.1:8894}"
LANG_HINT="${LANG_HINT:-ru}"

PASS=0
FAIL=0

ok()   { echo "  ✓ $*"; PASS=$((PASS+1)); }
fail() { echo "  ✗ $*"; FAIL=$((FAIL+1)); }
info() { echo "  ℹ $*"; }

echo "═══════════════════════════════════════════════════════════════"
echo "  Noetix E1 Pipeline — Pre-flight Test"
echo "  e1_server : $E1_SERVER_URL"
echo "  bridge    : $BRIDGE_URL"
echo "  nlgw      : $NLGW_URL"
echo "═══════════════════════════════════════════════════════════════"

# ── 1. e1_server reachable (onboard Jetson) ────────────────────────────
echo ""
echo "[1/7] e1_server /health  (onboard Jetson wrapper)"
resp=$(curl -s -m 3 "$E1_SERVER_URL/health")
if echo "$resp" | grep -q '"status"'; then
    transport=$(echo "$resp" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("transport","?"))' 2>/dev/null)
    mode=$(echo "$resp" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("mode_e1","?"))' 2>/dev/null)
    ok "e1_server alive — transport=$transport mode=$mode"
    if [ "$transport" = "sim" ]; then
        info "transport=sim → no real hardware plugged in. Set E1_TRANSPORT=ros2 on the Jetson."
    fi
else
    fail "e1_server unreachable at $E1_SERVER_URL"
    echo "        → ssh noetix@192.168.55.101 && bash scripts/start_e1_server.sh"
    echo "        → check Jetson is on the same 192.168.55.0/24 subnet"
    echo ""
    echo "  Can't continue without the onboard server."
    exit 1
fi

# ── 2. e1_server state has position (/odom is wired) ──────────────────
echo ""
echo "[2/7] e1_server /api/state  (telemetry from /odom)"
resp=$(curl -s -m 3 "$E1_SERVER_URL/api/state")
pos_x=$(echo "$resp" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("pos_x",d.get("position_x","?")))' 2>/dev/null)
bat=$(echo "$resp" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("battery_pct",d.get("battery","?")))' 2>/dev/null)
if [ "$pos_x" != "?" ] && [ -n "$pos_x" ]; then
    ok "telemetry present — pos_x=$pos_x battery=$bat"
else
    fail "telemetry empty — /odom not publishing? (response: $(echo "$resp" | head -c 160))"
fi

# ── 3. Trust Layer bridge reachable ───────────────────────────────────
echo ""
echo "[3/7] bridge /health  (Trust Layer safety layer)"
resp=$(curl -s -m 3 "$BRIDGE_URL/health")
adapter=$(echo "$resp" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("adapter","?"))' 2>/dev/null)
connected=$(echo "$resp" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("connected","?"))' 2>/dev/null)
rules_backend=$(echo "$resp" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("rules_backend","?"))' 2>/dev/null)
if [ "$adapter" = "e1" ]; then
    ok "bridge adapter=e1  connected=$connected  rules_backend=$rules_backend"
    if [ "$connected" != "True" ] && [ "$connected" != "true" ]; then
        fail "bridge reports connected=$connected — watchdog will cut commands"
    fi
    if [ "$rules_backend" = "fallback_6_rules" ]; then
        info "rules_backend=fallback_6_rules — ActionGate not loaded. Mount libs/ for full 137 rules."
    fi
else
    fail "bridge adapter=$adapter (expected 'e1') — check ADAPTER_TYPE in start_e1_bridge.sh"
fi

# ── 4. Velocity command through safety pipeline ───────────────────────
echo ""
echo "[4/7] POST /robot/move  vx=0.2  (via Trust Layer gate)"
resp=$(curl -sX POST "$BRIDGE_URL/robot/move" \
    -H 'Content-Type: application/json' \
    -d '{"vx":0.2,"vy":0,"wz":0}' --max-time 4)
decision=$(echo "$resp" | python3 -c 'import sys,json;d=json.load(sys.stdin);g=d.get("gate",{});print(g.get("decision","?"))' 2>/dev/null)
send_status=$(echo "$resp" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("send",{}).get("status","?"))' 2>/dev/null)
if [ "$decision" = "ALLOW" ] && [ "$send_status" = "ok" ]; then
    ok "gate=ALLOW  send=ok"
else
    fail "gate=$decision  send=$send_status  (raw: $(echo "$resp" | head -c 200))"
fi
sleep 1

# ── 5. Emergency stop ──────────────────────────────────────────────────
echo ""
echo "[5/7] POST /robot/stop  (emergency)"
resp=$(curl -sX POST "$BRIDGE_URL/robot/stop" --max-time 3)
if echo "$resp" | grep -q '"status":\s*"stopped"\|"status":\s*"ok"'; then
    ok "stop acknowledged"
else
    fail "stop unexpected: $(echo "$resp" | head -c 200)"
fi

# ── 6. Chat layer (only if nlgw is up) ────────────────────────────────
echo ""
echo "[6/7] POST /v1/execute 'иди вперёд'  (chat → intent → plan → gate → bridge)"
if curl -s -m 2 "$NLGW_URL/health" >/dev/null 2>&1; then
    resp=$(curl -sX POST "$NLGW_URL/v1/execute" \
        -H 'Content-Type: application/json' \
        -d "{\"message\":\"иди вперёд\",\"robot_id\":\"e1-01\",\"lang\":\"$LANG_HINT\"}" \
        --max-time 20)
    ok_flag=$(echo "$resp" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("ok"))' 2>/dev/null)
    intent=$(echo "$resp" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("intent",{}).get("intent",""))' 2>/dev/null)
    explanation=$(echo "$resp" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("explanation",""))' 2>/dev/null)
    if [ "$ok_flag" = "True" ]; then
        ok "chat OK — intent=$intent"
        info "response: $(echo "$explanation" | head -c 120)"
        if echo "$explanation" | grep -q "не сдвинулся\|did not move"; then
            fail "honest reporter says the robot did NOT physically move"
            info "check: mode_e1=walking?  /odom topic publishing?  repeater alive in e1_server?"
        fi
    else
        fail "chat pipeline failed: $(echo "$resp" | head -c 200)"
    fi
else
    info "nl_command_gateway not up at $NLGW_URL — skipping chat test"
fi

# ── 7. Episode capture wrote the episode ──────────────────────────────
echo ""
echo "[7/7] /v1/episodes/stats  (Trust Layer decision audit)"
if curl -s -m 2 "$NLGW_URL/health" >/dev/null 2>&1; then
    resp=$(curl -s -m 3 "$NLGW_URL/v1/episodes/stats")
    total=$(echo "$resp" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("total",0))' 2>/dev/null)
    if [ -n "$total" ] && [ "$total" != "0" ]; then
        ok "episodes captured: $total"
    else
        info "no episodes yet — send a command through chat to populate"
    fi
else
    info "nl_command_gateway offline — skipping"
fi

# ── Summary ────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════"
if [ "$FAIL" -eq 0 ]; then
    echo "  PASS: $PASS checks green. E1 pipeline is ready for the operator."
else
    echo "  FAIL: $FAIL checks failed, $PASS passed."
    echo ""
    echo "  Debug checklist:"
    echo "    tail -F ~/trust-layer-robot-bridge/bridge.log"
    echo "    ssh noetix@192.168.55.101 'tail -F /tmp/e1_server.log'"
    echo "    curl -s $BRIDGE_URL/health | jq ."
    echo "    curl -s $E1_SERVER_URL/api/state | jq ."
    exit 1
fi
echo "═══════════════════════════════════════════════════════════════"
