package main

import (
	"math"
	"time"
)

// ActionRequest is the incoming action from Trust Layer.
type ActionRequest struct {
	ActionType     string                 `json:"action_type"`
	RobotID        string                 `json:"robot_id"`
	Target         string                 `json:"target,omitempty"`
	TargetPosition map[string]float64     `json:"target_position,omitempty"`
	SpeedMps       float64                `json:"target_speed_mps,omitempty"`
	TargetSpeedMps float64                `json:"speed_mps,omitempty"`
	Params         map[string]interface{} `json:"params,omitempty"`
	SnapshotID     string                 `json:"snapshot_id,omitempty"`
}

// GateCheckResult is the outcome of the safety gate check.
type GateCheckResult struct {
	Decision     string  `json:"decision"`      // ALLOW | DENY | LIMIT
	Reason       string  `json:"reason"`
	RuleID       string  `json:"rule_id"`
	AuditRef     string  `json:"audit_ref"`
	ClampedSpeed float64 `json:"clamped_speed"`
	LatencyUs    int64   `json:"latency_us"`
}

// Safety thresholds — match Python SafetyPipeline exactly
const (
	MaxSpeedMps      = 1.2
	MaxSpeedAdvisory = 0.8
	MaxAngularRps    = 1.0
	BatteryCritical  = 5.0  // Below -> DENY movement
	TiltLimitDeg     = 25.0 // Above -> DENY movement
	HumanStopM       = 1.5  // Below -> DENY
	HumanSlowM       = 2.5  // Below -> LIMIT speed to 0.3
	HumanSlowSpeed   = 0.3
)

// Allowed action types for movement (need safety checks)
var movementActions = map[string]bool{
	"navigate_to":      true,
	"move_to":          true,
	"move_forward":     true,
	"move_backward":    true,
	"move_relative":    true,
	"move_left":        true,
	"move_right":       true,
	"rotate":           true,
	"follow":           true,
	"patrol":           true,
	"escort_to_target": true,
}

// Safe actions that always pass
var safeActions = map[string]bool{
	"stop":           true,
	"e_stop":         true,
	"wait":           true,
	"say":            true,
	"announce":       true,
	"wave":           true,
	"nod":            true,
	"bow":            true,
	"describe_scene": true,
	"report_status":  true,
	"look_at":        true,
	"greet":          true,
}

// SafetyGate performs final validation on robot.
type SafetyGate struct {
	lastBattery   float64
	lastTilt      float64
	lastHumanDist float64
	lastUpdate    time.Time
}

// NewSafetyGate creates a new gate.
func NewSafetyGate() *SafetyGate {
	return &SafetyGate{
		lastBattery:   100.0,
		lastTilt:      0.0,
		lastHumanDist: 999.0,
	}
}

// UpdateTelemetry updates the cached robot sensor values.
// Called from /robot/state or embedded in action params.
func (g *SafetyGate) UpdateTelemetry(battery, tilt, humanDist float64) {
	if battery >= 0 {
		g.lastBattery = battery
	}
	if tilt >= 0 {
		g.lastTilt = tilt
	}
	if humanDist >= 0 {
		g.lastHumanDist = humanDist
	}
	g.lastUpdate = time.Now()
}

// Check validates an action request against all 131 compiled safety rules.
func (g *SafetyGate) Check(req *ActionRequest) GateCheckResult {
	// Safe actions always pass — stop/e_stop must never be blocked
	if safeActions[req.ActionType] {
		return GateCheckResult{
			Decision: "ALLOW",
			Reason:   "safe_action",
		}
	}

	// Unknown action -> DENY
	if !movementActions[req.ActionType] && !safeActions[req.ActionType] {
		return GateCheckResult{
			Decision: "DENY",
			Reason:   "unknown_action:" + req.ActionType,
			RuleID:   "GATE-UNKNOWN",
		}
	}

	// Movement action — build state and evaluate all rules
	state := g.BuildStateFromAction(req)
	result := g.EvaluateRules(state)

	// If rules passed but speed needs advisory clamping, apply it
	if result.Decision == "ALLOW" {
		speed := math.Max(req.SpeedMps, req.TargetSpeedMps)
		if speed > MaxSpeedAdvisory {
			result.ClampedSpeed = MaxSpeedAdvisory
		} else {
			result.ClampedSpeed = speed
		}
	}

	return result
}
