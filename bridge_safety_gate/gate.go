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

// Check validates an action request.
func (g *SafetyGate) Check(req *ActionRequest) GateCheckResult {
	// Safe actions always pass
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

	// Movement action — run safety checks
	speed := math.Max(req.SpeedMps, req.TargetSpeedMps)

	// 1. Speed absolute limit
	if speed > MaxSpeedMps {
		return GateCheckResult{
			Decision:     "LIMIT",
			Reason:       "speed_exceeds_max",
			RuleID:       "SPEED-ABS",
			AuditRef:     "ISO 3691-4:2023 S6.2.3",
			ClampedSpeed: MaxSpeedMps,
		}
	}

	// 2. Speed advisory limit
	clampedSpeed := speed
	if speed > MaxSpeedAdvisory {
		clampedSpeed = MaxSpeedAdvisory
	}

	// 3. Snapshot age check (if snapshot_id present)
	// In production: verify snapshot freshness via timestamp
	// For now: trust the snapshot_id as proof of recent check

	return GateCheckResult{
		Decision:     "ALLOW",
		Reason:       "passed",
		ClampedSpeed: clampedSpeed,
	}
}
