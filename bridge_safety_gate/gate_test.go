package main

import "testing"

func TestSafeActionAlwaysAllowed(t *testing.T) {
	g := NewSafetyGate()
	for _, action := range []string{"stop", "e_stop", "wait", "say", "wave", "nod"} {
		r := g.Check(&ActionRequest{ActionType: action})
		if r.Decision != "ALLOW" {
			t.Errorf("safe action %s should ALLOW, got %s", action, r.Decision)
		}
	}
}

func TestUnknownActionDenied(t *testing.T) {
	g := NewSafetyGate()
	r := g.Check(&ActionRequest{ActionType: "hack_motors"})
	if r.Decision != "DENY" {
		t.Errorf("unknown action should DENY, got %s", r.Decision)
	}
}

func TestSpeedOverLimitClamped(t *testing.T) {
	g := NewSafetyGate()
	r := g.Check(&ActionRequest{
		ActionType: "navigate_to",
		SpeedMps:   5.0,
	})
	if r.Decision != "LIMIT" {
		t.Errorf("speed 5.0 should LIMIT, got %s (%s)", r.Decision, r.Reason)
	}
	if r.ClampedSpeed > MaxSpeedMps {
		t.Errorf("clamped speed should be <= %f, got %f", MaxSpeedMps, r.ClampedSpeed)
	}
}

func TestNormalSpeedAllowed(t *testing.T) {
	g := NewSafetyGate()
	r := g.Check(&ActionRequest{
		ActionType: "navigate_to",
		SpeedMps:   0.5,
	})
	if r.Decision == "DENY" {
		t.Errorf("normal speed 0.5 should not DENY, got %s: %s", r.Decision, r.Reason)
	}
}

func TestMovementActionsChecked(t *testing.T) {
	g := NewSafetyGate()
	for _, action := range []string{"navigate_to", "move_forward", "patrol", "follow"} {
		r := g.Check(&ActionRequest{ActionType: action, SpeedMps: 0.3})
		if r.Decision == "DENY" {
			t.Errorf("normal movement %s should not DENY, got %s: %s", action, r.Decision, r.Reason)
		}
	}
}

// ═══════════════════════════════════════════════════════════════════════
// Rule Engine Tests — verify all key ISO/OSHA rules evaluate correctly
// ═══════════════════════════════════════════════════════════════════════

func TestRuleEngine_EmergencyStop(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Bools["is_e_stopped"] = true
	r := g.EvaluateRules(state)
	if r.Decision != "DENY" {
		t.Errorf("e-stop should DENY, got %s", r.Decision)
	}
	if r.RuleID != "ISO3691-4-ESTOP-001" {
		t.Errorf("e-stop rule ID should be ISO3691-4-ESTOP-001, got %s", r.RuleID)
	}
}

func TestRuleEngine_SafetySystemFault(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Bools["safety_system_ok"] = false
	r := g.EvaluateRules(state)
	if r.Decision != "DENY" {
		t.Errorf("safety fault should DENY, got %s", r.Decision)
	}
}

func TestRuleEngine_BatteryCritical(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Floats["battery_pct"] = 3.0
	r := g.EvaluateRules(state)
	if r.Decision != "DENY" {
		t.Errorf("battery 3%% should DENY, got %s (%s)", r.Decision, r.Reason)
	}
}

func TestRuleEngine_TiltExcessive(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Floats["tilt_deg"] = 22.0
	r := g.EvaluateRules(state)
	if r.Decision != "DENY" {
		t.Errorf("tilt 22 deg should DENY, got %s (%s)", r.Decision, r.Reason)
	}
}

func TestRuleEngine_HumanTooClose_Stop(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Floats["nearest_human_m"] = 1.0
	r := g.EvaluateRules(state)
	if r.Decision != "DENY" {
		t.Errorf("human at 1.0m should DENY, got %s (%s)", r.Decision, r.Reason)
	}
}

func TestRuleEngine_HumanNearby_Clamp(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Floats["nearest_human_m"] = 2.0
	r := g.EvaluateRules(state)
	if r.Decision != "LIMIT" {
		t.Errorf("human at 2.0m should LIMIT, got %s (%s)", r.Decision, r.Reason)
	}
	if r.ClampedSpeed > 0.3 {
		t.Errorf("should clamp to 0.3, got %f", r.ClampedSpeed)
	}
}

func TestRuleEngine_SpeedClamp(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Floats["speed_mps"] = 1.5
	r := g.EvaluateRules(state)
	if r.Decision != "LIMIT" {
		t.Errorf("speed 1.5 should LIMIT, got %s (%s)", r.Decision, r.Reason)
	}
	if r.ClampedSpeed > MaxSpeedAdvisory {
		t.Errorf("should clamp to advisory, got %f", r.ClampedSpeed)
	}
}

func TestRuleEngine_Charging_Deny(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Bools["is_charging"] = true
	r := g.EvaluateRules(state)
	if r.Decision != "DENY" {
		t.Errorf("charging should DENY movement, got %s", r.Decision)
	}
}

func TestRuleEngine_ObstacleInPath(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Bools["obstacle_in_path"] = true
	r := g.EvaluateRules(state)
	if r.Decision != "DENY" {
		t.Errorf("obstacle_in_path should DENY, got %s", r.Decision)
	}
}

func TestRuleEngine_ControlSystemFailure(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Bools["control_system_failure"] = true
	r := g.EvaluateRules(state)
	if r.Decision != "DENY" {
		t.Errorf("control_system_failure should DENY, got %s", r.Decision)
	}
}

func TestRuleEngine_CommLoss(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Bools["comm_ok"] = false
	r := g.EvaluateRules(state)
	if r.Decision != "DENY" {
		t.Errorf("comm_ok=false should DENY, got %s", r.Decision)
	}
}

func TestRuleEngine_CollaborativeSpeedClamp(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Bools["collaborative_mode"] = true
	r := g.EvaluateRules(state)
	if r.Decision != "LIMIT" {
		t.Errorf("collaborative_mode should LIMIT, got %s (%s)", r.Decision, r.Reason)
	}
	if r.ClampedSpeed > 0.25 {
		t.Errorf("collaborative clamp should be 0.25, got %f", r.ClampedSpeed)
	}
}

func TestRuleEngine_TemperatureLow(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Floats["temperature_c"] = 2.0
	r := g.EvaluateRules(state)
	if r.Decision != "DENY" {
		t.Errorf("temp 2C should DENY, got %s (%s)", r.Decision, r.Reason)
	}
}

func TestRuleEngine_TemperatureHigh(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Floats["temperature_c"] = 40.0
	r := g.EvaluateRules(state)
	if r.Decision != "DENY" {
		t.Errorf("temp 40C should DENY, got %s (%s)", r.Decision, r.Reason)
	}
}

func TestRuleEngine_AllClear(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	// No flags set, no thresholds exceeded → ALLOW
	r := g.EvaluateRules(state)
	if r.Decision != "ALLOW" {
		t.Errorf("clean state should ALLOW, got %s (%s)", r.Decision, r.Reason)
	}
}

func TestRuleEngine_MultipleClamps_LowestWins(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Floats["speed_mps"] = 1.5                // triggers OSHA 2.2 clamp (no), and advisory 0.8 clamp
	state.Bools["collaborative_mode"] = true        // triggers 0.25 clamp
	state.Floats["nearest_human_m"] = 4.0           // triggers 0.3 clamp (personnel warning zone)
	r := g.EvaluateRules(state)
	if r.Decision != "LIMIT" {
		t.Errorf("multiple clamps should LIMIT, got %s", r.Decision)
	}
	// Collaborative 0.25 is the tightest among the non-charging clamps
	// but charging zone (0.1) is not set, so 0.25 should win
	if r.ClampedSpeed > 0.25 {
		t.Errorf("lowest clamp should be 0.25 (collaborative), got %f", r.ClampedSpeed)
	}
}

func TestRuleEngine_StopTakesPriorityOverClamp(t *testing.T) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Floats["speed_mps"] = 1.5             // would trigger clamp
	state.Bools["is_e_stopped"] = true           // but e-stop → immediate DENY
	r := g.EvaluateRules(state)
	if r.Decision != "DENY" {
		t.Errorf("stop should override clamp, got %s", r.Decision)
	}
}

func TestCheck_IntegratedWithRuleEngine(t *testing.T) {
	g := NewSafetyGate()
	// Simulate battery at 3% via telemetry cache
	g.UpdateTelemetry(3.0, 0.0, 999.0)
	r := g.Check(&ActionRequest{
		ActionType: "navigate_to",
		SpeedMps:   0.3,
	})
	if r.Decision != "DENY" {
		t.Errorf("battery 3%% via telemetry should DENY navigate, got %s (%s)", r.Decision, r.Reason)
	}
}

func TestCheck_ParamsPassedToRuleEngine(t *testing.T) {
	g := NewSafetyGate()
	r := g.Check(&ActionRequest{
		ActionType: "navigate_to",
		SpeedMps:   0.3,
		Params: map[string]interface{}{
			"is_charging": true,
		},
	})
	if r.Decision != "DENY" {
		t.Errorf("is_charging=true should DENY navigate, got %s (%s)", r.Decision, r.Reason)
	}
}

func TestRuleCount(t *testing.T) {
	if len(AllRules) < 60 {
		t.Errorf("expected at least 60 compiled rules, got %d", len(AllRules))
	}
}

func BenchmarkGateCheck(b *testing.B) {
	g := NewSafetyGate()
	req := &ActionRequest{
		ActionType: "navigate_to",
		SpeedMps:   0.5,
		Target:     "booth_a",
	}
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		g.Check(req)
	}
}

func BenchmarkRuleEngineEvaluate(b *testing.B) {
	g := NewSafetyGate()
	state := NewRobotState()
	state.Floats["speed_mps"] = 0.5
	state.Floats["battery_pct"] = 80.0
	state.Floats["nearest_human_m"] = 10.0
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		g.EvaluateRules(state)
	}
}
