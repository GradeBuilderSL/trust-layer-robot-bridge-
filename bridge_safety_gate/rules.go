// Compiled from YAML rule files in libs/ontology/rules/*.yaml.
// DO NOT EDIT MANUALLY — regenerate with tools/generate_go_rules.py.
//
// Source files (13):
//   iso_3691_4.yaml, iso_10218.yaml, iso_13482.yaml, iso_12100.yaml,
//   iso_13849.yaml, eu_machinery_2023_1230.yaml, eu_ai_act.yaml,
//   iec_62443.yaml, hri.yaml, hardware.yaml, nav_safety.yaml,
//   warehouse_safety.yaml, head_camera_safety.yaml
//
// Rule count: 131 (emergency: 20, hard: 68, policy/advisory: 43)
// Only emergency + hard rules are enforced here (stop/deny/clamp).
// Policy/advisory rules contribute penalty scores in the Python pipeline.

package main

// Rule represents a single compiled safety rule.
type Rule struct {
	ID         string
	Name       string
	Field      string
	Op         string  // "lt", "gt", "eq", "ne", "eq_bool"
	Value      float64 // numeric threshold
	ValueBool  bool    // boolean expected value (for eq_bool)
	Action     string  // "stop", "clamp", "deny"
	ClampSpeed float64 // max speed when Action == "clamp"
	AuditRef   string
	Layer      string // "emergency", "hard"
}

// AllRules contains deterministic safety rules compiled from YAML.
// Ordered by priority: emergency first, then hard constraints.
// Compound conditions (AND of multiple fields) are split into the
// most critical single-field check that the gate can evaluate with
// the state it receives. Zone/entity conditions that require world
// model data are evaluated in the Python SafetyPipeline upstream.
var AllRules = []Rule{

	// ════════════════════════════════════════════════════════════════════
	// EMERGENCY RULES — immediate stop, override everything
	// ════════════════════════════════════════════════════════════════════

	// ── ISO 3691-4:2023 ─────────────────────────────────────────────
	{ID: "ISO3691-4-ESTOP-001", Name: "Emergency stop activated",
		Field: "is_e_stopped", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 3691-4:2023 §4.4.1", Layer: "emergency"},

	{ID: "ISO3691-4-SAFETY-FAULT-001", Name: "Safety system fault",
		Field: "safety_system_ok", Op: "eq_bool", ValueBool: false,
		Action: "stop", AuditRef: "ISO 3691-4:2023 §4.4.1", Layer: "emergency"},

	{ID: "ISO3691-4-PERSONNEL-DETECT-FAIL-001", Name: "Personnel detection failure",
		Field: "personnel_detection_ok", Op: "eq_bool", ValueBool: false,
		Action: "stop", AuditRef: "ISO 3691-4:2023 §4.6", Layer: "emergency"},

	// ── ISO 10218 ───────────────────────────────────────────────────
	{ID: "ISO-SAFETY-CTRL-FAILURE", Name: "Safety control failure",
		Field: "safety_control_failure", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 10218-1:2011 §5.4.1", Layer: "emergency"},

	{ID: "ISO-SINGLE-FAULT-SAFE-STATE", Name: "Single fault detected",
		Field: "single_fault_detected", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 10218-1:2011 §5.4.2", Layer: "emergency"},

	{ID: "ISO-CONTACT-TORQUE-STOP", Name: "Joint torque contact threshold",
		Field: "joint_torque_exceeded", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 10218-1:2011 §5.5", Layer: "emergency"},

	// ── EU Machinery Regulation 2023/1230 ───────────────────────────
	{ID: "EU-MR-EMERGENCY-STOP-001", Name: "EU emergency stop pressed",
		Field: "emergency_stop_pressed", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "EU MR 2023/1230 Annex III §1.2.4.3", Layer: "emergency"},

	// ── HRI ─────────────────────────────────────────────────────────
	{ID: "HRI-PERCEPTION-MISMATCH-STOP", Name: "Perception error with human",
		Field: "perception_error", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 10218-1:2011 §5.4.2", Layer: "emergency"},

	{ID: "HRI-DYNAMIC-SAFETY-STOP", Name: "Dynamic safety distance violated",
		Field: "dynamic_clearance_ok", Op: "eq_bool", ValueBool: false,
		Action: "stop", AuditRef: "ISO 10218-2:2011 §5.10.3", Layer: "emergency"},

	// ── Hardware ────────────────────────────────────────────────────
	{ID: "HW-LOW-BATTERY-AUTO-CROUCH", Name: "Battery <10% emergency",
		Field: "battery_pct", Op: "lt", Value: 10,
		Action: "stop", AuditRef: "Unitree H1 manual", Layer: "emergency"},

	{ID: "HW-DAMPING-MODE-OVERRIDE", Name: "Hardware safety mode active",
		Field: "hardware_safety_mode", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "Unitree Go2/H1 manual", Layer: "emergency"},

	{ID: "HW-CALIBRATION-BLOCK", Name: "Calibration in progress",
		Field: "calibration_in_progress", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "Unitree Go2/H1 manual", Layer: "emergency"},

	{ID: "HW-SYSTEM-ABNORMALITY", Name: "System abnormality",
		Field: "system_abnormality", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "Unitree Go2/H1 manual", Layer: "emergency"},

	// ── Warehouse ───────────────────────────────────────────────────
	{ID: "EMRG-ESTOP", Name: "E-stop signal",
		Field: "is_e_stopped", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "OSHA+ISO", Layer: "emergency"},

	{ID: "EMRG-CRITICAL-BATTERY", Name: "Battery critical <5%",
		Field: "battery_pct", Op: "lt", Value: 5,
		Action: "stop", AuditRef: "OSHA+Hardware", Layer: "emergency"},

	{ID: "EMRG-SENSOR-FAILURE", Name: "Sensor tracking failure",
		Field: "tracking_error", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "Incident #8", Layer: "emergency"},

	// ── Navigation ──────────────────────────────────────────────────
	{ID: "NAV-CTRL-SERVER-DOWN", Name: "Nav controller server down",
		Field: "nav_server_alive", Op: "eq_bool", ValueBool: false,
		Action: "stop", AuditRef: "Nav2 NAV_SCENARIO_07", Layer: "emergency"},

	// ── Head/Camera ─────────────────────────────────────────────────
	{ID: "HEAD-ESTOP-001", Name: "No head movement during e-stop",
		Field: "is_e_stopped", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 10218-1:2011 §5.4.1", Layer: "emergency"},

	{ID: "HEAD-SENSOR-FAILURE-001", Name: "No head movement on sensor failure",
		Field: "sensor_ok", Op: "eq_bool", ValueBool: false,
		Action: "stop", AuditRef: "ISO 10218-1:2011 §5.4.2", Layer: "emergency"},

	// ════════════════════════════════════════════════════════════════════
	// HARD CONSTRAINT RULES — deny or clamp
	// ════════════════════════════════════════════════════════════════════

	// ── ISO 3691-4:2023 — Speed & stability ─────────────────────────
	{ID: "ISO3691-4-STABILITY-TILT-001", Name: "Tilt limit exceeded",
		Field: "tilt_deg", Op: "gt", Value: 20.0,
		Action: "stop", AuditRef: "ISO 3691-4:2023 §4.3", Layer: "hard"},

	{ID: "ISO3691-4-SPEED-OPERATING-001", Name: "Speed limit operating zone 1.2 m/s",
		Field: "speed_mps", Op: "gt", Value: 1.2,
		Action: "clamp", ClampSpeed: 1.2, AuditRef: "ISO 3691-4:2023 §4.5.2", Layer: "hard"},

	{ID: "ISO3691-4-OVERSPEED-001", Name: "Overspeed detection",
		Field: "overspeed_flag", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 3691-4:2023 §4.5.3", Layer: "hard"},

	{ID: "ISO3691-4-CHARGE-SAFETY-001", Name: "No motion during charge",
		Field: "is_charging", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 3691-4:2023 §4.8", Layer: "hard"},

	// ── ISO 3691-4:2023 — Human proximity ───────────────────────────
	{ID: "ISO3691-4-HUMAN-001", Name: "Human too close (<1.5m) stop",
		Field: "nearest_human_m", Op: "lt", Value: 1.5,
		Action: "stop", AuditRef: "ISO 3691-4:2023 §4.3.4", Layer: "hard"},

	{ID: "ISO3691-4-HUMAN-002", Name: "Human proximity (<2.5m) slowdown",
		Field: "nearest_human_m", Op: "lt", Value: 2.5,
		Action: "clamp", ClampSpeed: 0.3, AuditRef: "ISO 3691-4:2023 §4.3.4", Layer: "hard"},

	{ID: "ISO3691-4-PERSONNEL-SLOW-001", Name: "Personnel warning zone speed",
		Field: "nearest_human_m", Op: "lt", Value: 5.0,
		Action: "clamp", ClampSpeed: 0.3, AuditRef: "ISO 3691-4:2023 §4.6", Layer: "hard"},

	// ── ISO 3691-4:2023 — Obstacles ─────────────────────────────────
	{ID: "ISO3691-4-OBSTACLE-001", Name: "Obstacle in forward path",
		Field: "obstacle_in_path", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 3691-4:2023 §4.7", Layer: "hard"},

	{ID: "ISO3691-4-OBSTACLE-CLEARANCE-001", Name: "Min clearance 0.5m",
		Field: "obstacle_clearance_m", Op: "lt", Value: 0.5,
		Action: "stop", AuditRef: "ISO 3691-4:2023 §4.7", Layer: "hard"},

	// ── ISO 3691-4:2023 — Charging ──────────────────────────────────
	{ID: "ISO3691-4-CHARGE-ZONE-001", Name: "Charging zone speed limit",
		Field: "in_charging_zone", Op: "eq_bool", ValueBool: true,
		Action: "clamp", ClampSpeed: 0.1, AuditRef: "ISO 3691-4:2023 §4.8", Layer: "hard"},

	// ── ISO 3691-4:2023 — Load ──────────────────────────────────────
	{ID: "ISO3691-4-LOAD-STABILITY-001", Name: "Load carrying speed limit",
		Field: "carrying_load", Op: "eq_bool", ValueBool: true,
		Action: "clamp", ClampSpeed: 0.8, AuditRef: "ISO 3691-4:2023 §4.3", Layer: "hard"},

	// ── ISO 10218 — Guards & separation ─────────────────────────────
	{ID: "ISO-MIN-SEPARATION", Name: "Min 0.5m separation from humans",
		Field: "clearance_to_human_m", Op: "lt", Value: 0.5,
		Action: "stop", AuditRef: "ISO 10218-1:2011 §5.5.1", Layer: "hard"},

	{ID: "ISO-POWER-RESTORE-STATIC", Name: "No motion on power restore",
		Field: "power_just_restored", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 10218-1:2011 §5.2.2", Layer: "hard"},

	// ── ISO 10218 — Collaborative ───────────────────────────────────
	{ID: "ISO10218-COLLAB-SPEED-001", Name: "Collaborative speed 0.25 m/s",
		Field: "collaborative_mode", Op: "eq_bool", ValueBool: true,
		Action: "clamp", ClampSpeed: 0.25, AuditRef: "ISO 10218-2:2025 §11.6", Layer: "hard"},

	{ID: "ISO10218-SSM-001", Name: "SSM protective distance violated",
		Field: "protective_distance_violated", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 10218-2:2025 §11.6.4", Layer: "hard"},

	// ── ISO 10218 — Force limits ────────────────────────────────────
	{ID: "ISO10218-PFL-HEAD-001", Name: "Head contact force >130N",
		Field: "contact_force_head_n", Op: "gt", Value: 130,
		Action: "stop", AuditRef: "ISO 10218-2:2025 §11.6.5", Layer: "hard"},

	{ID: "ISO10218-PFL-CHEST-001", Name: "Chest contact force >150N",
		Field: "contact_force_chest_n", Op: "gt", Value: 150,
		Action: "stop", AuditRef: "ISO 10218-2:2025 §11.6.5", Layer: "hard"},

	{ID: "ISO10218-PFL-HAND-001", Name: "Hand contact force >235N",
		Field: "contact_force_hand_n", Op: "gt", Value: 235,
		Action: "stop", AuditRef: "ISO 10218-2:2025 §11.6.5", Layer: "hard"},

	{ID: "ISO10218-SAFEGUARD-RESET-001", Name: "Manual reset required after protective stop",
		Field: "protective_stop_needs_reset", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 10218-1:2025 §5.4", Layer: "hard"},

	// ── ISO 13482 — Personal care ───────────────────────────────────
	{ID: "ISO13482-STABILITY-001", Name: "Stability margin critically low",
		Field: "stability_margin", Op: "lt", Value: 0.1,
		Action: "stop", AuditRef: "ISO 13482:2014 §5.4", Layer: "hard"},

	{ID: "ISO13482-ENTRAPMENT-001", Name: "Entrapment risk",
		Field: "entrapment_risk", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 13482:2014 §5.6", Layer: "hard"},

	// ── ISO 12100 — Mechanical hazards ──────────────────────────────
	{ID: "ISO12100-MECH-CRUSH-001", Name: "Crushing hazard",
		Field: "crush_hazard_present", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 12100:2010 §6.2.2", Layer: "hard"},

	{ID: "ISO12100-MECH-SHEAR-001", Name: "Shearing hazard",
		Field: "shear_hazard_present", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 12100:2010 §6.2.2", Layer: "hard"},

	{ID: "ISO12100-MECH-ENTANGLE-001", Name: "Entanglement hazard",
		Field: "entanglement_hazard_present", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 12100:2010 §6.2.2", Layer: "hard"},

	// ── ISO 13849 — Performance levels ──────────────────────────────
	{ID: "ISO13849-PLC-ESTOP-001", Name: "E-stop PL below PLe",
		Field: "estop_pl_insufficient", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "ISO 13849-1:2023 §6.2", Layer: "hard"},

	// ── EU Machinery Regulation ─────────────────────────────────────
	{ID: "EU-MR-CYBER-001", Name: "Safety integrity violation",
		Field: "safety_integrity_ok", Op: "eq_bool", ValueBool: false,
		Action: "stop", AuditRef: "EU MR 2023/1230 Annex III §1.1.9", Layer: "hard"},

	{ID: "EU-MR-CONTROL-FAILURE-001", Name: "Control system failure",
		Field: "control_system_failure", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "EU MR 2023/1230 Annex III §1.2.1", Layer: "hard"},

	{ID: "EU-MR-STARTUP-001", Name: "Unexpected startup prevention",
		Field: "startup_unexpected", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "EU MR 2023/1230 Annex III §1.2.3", Layer: "hard"},

	{ID: "EU-MR-COMM-FAILURE-001", Name: "Communication failure",
		Field: "communication_failure", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "EU MR 2023/1230 Annex III §1.2.1", Layer: "hard"},

	// ── IEC 62443 — Cybersecurity ───────────────────────────────────
	{ID: "IEC62443-INTEGRITY-001", Name: "Safety logic integrity violation",
		Field: "safety_logic_integrity_ok", Op: "eq_bool", ValueBool: false,
		Action: "stop", AuditRef: "IEC 62443-3-3 SR 3.6", Layer: "hard"},

	{ID: "IEC62443-AUTH-001", Name: "Unauthorized remote control",
		Field: "control_channel_secure", Op: "eq_bool", ValueBool: false,
		Action: "stop", AuditRef: "IEC 62443-3-3 SR 1.2", Layer: "hard"},

	{ID: "IEC62443-UPDATE-001", Name: "Unsafe software update state",
		Field: "software_update_unverified", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "IEC 62443-2-3 Req 6", Layer: "hard"},

	// ── Hardware ────────────────────────────────────────────────────
	{ID: "HW-OPERATING-TEMPERATURE-LOW", Name: "Temperature below 5C",
		Field: "temperature_c", Op: "lt", Value: 5,
		Action: "stop", AuditRef: "Unitree Go2/H1 manual", Layer: "hard"},

	{ID: "HW-OPERATING-TEMPERATURE-HIGH", Name: "Temperature above 35C",
		Field: "temperature_c", Op: "gt", Value: 35,
		Action: "stop", AuditRef: "Unitree Go2/H1 manual", Layer: "hard"},

	{ID: "HW-STAIR-MAX-ANGLE", Name: "Stair angle >40 deg",
		Field: "incline_deg", Op: "gt", Value: 40,
		Action: "stop", AuditRef: "Unitree Go2 manual", Layer: "hard"},

	{ID: "HW-STAIR-MAX-HEIGHT", Name: "Step height >16cm",
		Field: "step_height_cm", Op: "gt", Value: 16,
		Action: "stop", AuditRef: "Unitree Go2 manual", Layer: "hard"},

	// ── Warehouse / OSHA ────────────────────────────────────────────
	{ID: "OSHA-SPEED-LIMIT", Name: "Industrial speed limit 2.2 m/s",
		Field: "speed_mps", Op: "gt", Value: 2.2,
		Action: "clamp", ClampSpeed: 2.2, AuditRef: "OSHA 29 CFR 1910.178(n)(8)", Layer: "hard"},

	{ID: "OSHA-CHARGING-LOCK", Name: "No motion while charging",
		Field: "is_charging", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "OSHA Warehouse", Layer: "hard"},

	{ID: "OSHA-VISIBILITY", Name: "Sensor view required for movement",
		Field: "sensor_ok", Op: "eq_bool", ValueBool: false,
		Action: "stop", AuditRef: "OSHA Warehouse", Layer: "hard"},

	{ID: "OSHA-LOAD-CAPACITY", Name: "Load exceeds capacity",
		Field: "load_exceeds_capacity", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "OSHA osha2236", Layer: "hard"},

	{ID: "OSHA-LOAD-ELEVATED-TRANSIT", Name: "No transit with elevated load",
		Field: "load_elevated", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "OSHA osha2236", Layer: "hard"},

	// ── Navigation ──────────────────────────────────────────────────
	{ID: "NAV-LOGIC-DEADLOCK", Name: "Logic deadlock",
		Field: "stuck_in_logic_loop", Op: "eq_bool", ValueBool: true,
		Action: "stop", AuditRef: "Operational R-08", Layer: "hard"},

	{ID: "NAV-COMM-LOST-STOP", Name: "Communication loss",
		Field: "comm_ok", Op: "eq_bool", ValueBool: false,
		Action: "stop", AuditRef: "Operational", Layer: "hard"},

	// ── Original gate thresholds (kept for backward compatibility) ──
	{ID: "GATE-SPEED-ABS", Name: "Absolute speed limit",
		Field: "speed_mps", Op: "gt", Value: MaxSpeedMps,
		Action: "clamp", ClampSpeed: MaxSpeedMps, AuditRef: "ISO 3691-4:2023 §6.2.3", Layer: "hard"},

	{ID: "GATE-SPEED-ADVISORY", Name: "Advisory speed limit",
		Field: "speed_mps", Op: "gt", Value: MaxSpeedAdvisory,
		Action: "clamp", ClampSpeed: MaxSpeedAdvisory, AuditRef: "ISO 3691-4:2023 §4.5.2", Layer: "hard"},

	{ID: "GATE-BATTERY-CRITICAL", Name: "Battery critical",
		Field: "battery_pct", Op: "lt", Value: BatteryCritical,
		Action: "stop", AuditRef: "ISO 3691-4:2023 §4.4.4", Layer: "hard"},

	{ID: "GATE-TILT-LIMIT", Name: "Tilt limit exceeded",
		Field: "tilt_deg", Op: "gt", Value: TiltLimitDeg,
		Action: "stop", AuditRef: "ISO 3691-4:2023 §4.4.2", Layer: "hard"},

	{ID: "GATE-HUMAN-STOP", Name: "Human too close — stop",
		Field: "nearest_human_m", Op: "lt", Value: HumanStopM,
		Action: "stop", AuditRef: "ISO 3691-4:2023 §4.3.4", Layer: "hard"},

	{ID: "GATE-HUMAN-SLOW", Name: "Human proximity — slow down",
		Field: "nearest_human_m", Op: "lt", Value: HumanSlowM,
		Action: "clamp", ClampSpeed: HumanSlowSpeed, AuditRef: "ISO 3691-4:2023 §4.3.4", Layer: "hard"},
}
