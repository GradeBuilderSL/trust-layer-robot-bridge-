package main

// RobotState carries sensor/status fields for rule evaluation.
// The bridge populates this from the latest robot telemetry.
type RobotState struct {
	Floats map[string]float64
	Bools  map[string]bool
}

// NewRobotState creates an empty state with default safe values.
func NewRobotState() *RobotState {
	return &RobotState{
		Floats: make(map[string]float64),
		Bools:  make(map[string]bool),
	}
}

// EvaluateRules runs all compiled safety rules against the current
// robot state. Returns the most restrictive result:
//   - First "stop" rule match → immediate DENY (short-circuit)
//   - Multiple "clamp" matches → lowest clamped speed wins
//   - No matches → ALLOW
//
// Latency target: <50µs for full rule set (no allocations on happy path).
func (g *SafetyGate) EvaluateRules(state *RobotState) GateCheckResult {
	var (
		violations []string
		clampSpeed = -1.0
		clampRef   string
		clampID    string
	)

	for i := range AllRules {
		rule := &AllRules[i]
		matched := false

		switch rule.Op {
		case "lt":
			if val, ok := state.Floats[rule.Field]; ok && val < rule.Value {
				matched = true
			}
		case "gt":
			if val, ok := state.Floats[rule.Field]; ok && val > rule.Value {
				matched = true
			}
		case "eq":
			if val, ok := state.Floats[rule.Field]; ok && val == rule.Value {
				matched = true
			}
		case "ne":
			if val, ok := state.Floats[rule.Field]; ok && val != rule.Value {
				matched = true
			}
		case "eq_bool":
			if val, ok := state.Bools[rule.Field]; ok && val == rule.ValueBool {
				matched = true
			}
		}

		if !matched {
			continue
		}

		// Stop rules → immediate DENY, no further evaluation needed
		if rule.Action == "stop" || rule.Action == "deny" {
			return GateCheckResult{
				Decision: "DENY",
				Reason:   rule.Name,
				RuleID:   rule.ID,
				AuditRef: rule.AuditRef,
			}
		}

		// Clamp rules → accumulate, pick lowest speed
		if rule.Action == "clamp" {
			if clampSpeed < 0 || rule.ClampSpeed < clampSpeed {
				clampSpeed = rule.ClampSpeed
				clampRef = rule.AuditRef
				clampID = rule.ID
			}
			violations = append(violations, rule.ID)
		}
	}

	if clampSpeed >= 0 {
		reason := "speed_clamped"
		if len(violations) > 0 {
			reason = "speed_clamped:" + violations[0]
			if len(violations) > 1 {
				reason += "+more"
			}
		}
		return GateCheckResult{
			Decision:     "LIMIT",
			ClampedSpeed: clampSpeed,
			Reason:       reason,
			RuleID:       clampID,
			AuditRef:     clampRef,
		}
	}

	return GateCheckResult{Decision: "ALLOW", Reason: "all_rules_passed"}
}

// BuildStateFromAction extracts evaluable fields from an ActionRequest
// and merges them with the gate's cached telemetry.
func (g *SafetyGate) BuildStateFromAction(req *ActionRequest) *RobotState {
	state := NewRobotState()

	// Inject cached telemetry
	state.Floats["battery_pct"] = g.lastBattery
	state.Floats["tilt_deg"] = g.lastTilt
	state.Floats["nearest_human_m"] = g.lastHumanDist

	// Inject action-level fields
	speed := req.SpeedMps
	if req.TargetSpeedMps > speed {
		speed = req.TargetSpeedMps
	}
	state.Floats["speed_mps"] = speed

	// Extract params if present
	if req.Params != nil {
		for _, key := range []string{
			"temperature_c", "incline_deg", "step_height_cm",
			"stability_margin", "obstacle_clearance_m",
			"clearance_to_human_m", "contact_force_head_n",
			"contact_force_chest_n", "contact_force_hand_n",
		} {
			if v, ok := req.Params[key]; ok {
				if fv, ok := v.(float64); ok {
					state.Floats[key] = fv
				}
			}
		}
		for _, key := range []string{
			"is_e_stopped", "safety_system_ok", "personnel_detection_ok",
			"safety_control_failure", "single_fault_detected",
			"joint_torque_exceeded", "emergency_stop_pressed",
			"perception_error", "dynamic_clearance_ok",
			"hardware_safety_mode", "calibration_in_progress",
			"system_abnormality", "tracking_error", "nav_server_alive",
			"sensor_ok", "overspeed_flag", "is_charging",
			"obstacle_in_path", "in_charging_zone", "carrying_load",
			"power_just_restored", "collaborative_mode",
			"protective_distance_violated", "protective_stop_needs_reset",
			"entrapment_risk", "crush_hazard_present",
			"shear_hazard_present", "entanglement_hazard_present",
			"estop_pl_insufficient", "safety_integrity_ok",
			"control_system_failure", "startup_unexpected",
			"communication_failure", "safety_logic_integrity_ok",
			"control_channel_secure", "software_update_unverified",
			"load_exceeds_capacity", "load_elevated",
			"stuck_in_logic_loop", "comm_ok",
		} {
			if v, ok := req.Params[key]; ok {
				if bv, ok := v.(bool); ok {
					state.Bools[key] = bv
				}
			}
		}
	}

	return state
}
