"""RegulatoryIndex — maps rule IDs to their regulatory source documents.

Built from documents in _ontorobotic/raw_sources/:
  - ISO-10218-1:2011  (formal_safety_regulation/)
  - OSHA 29 CFR 1910 (regulations/osha/)
  - HRI research     (behavior_human_interaction/)
  - Nav2 docs        (ros_nav/)
  - Incident reports (incidents/)

Usage:
    from ontology.regulatory_index import RegulatoryIndex, lookup

    ref = lookup("ISO-GUARD-ZONE-STOP")
    # RegulatoryRef(rule_id='ISO-GUARD-ZONE-STOP',
    #               standard='ISO',
    #               document='ISO-10218-1:2011',
    #               section='5.2.1',
    #               obligation='mandatory',
    #               description='...')
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# =============================================================================
# DATA MODEL
# =============================================================================

@dataclass
class RegulatoryRef:
    rule_id: str
    standard: str           # ISO | OSHA | HRI | Nav2 | Hardware | Operational | Incident
    document: str           # source document name
    section: str            # section / page reference
    obligation: str         # mandatory | recommended | informational
    description: str        # plain-text statement from source
    citation: str = ""      # formal citation (e.g. "29 CFR 1910.178(n)(8)")
    raw_source_file: str = ""  # filename in _ontorobotic/raw_sources/
    version: str = ""       # optional version/year string, e.g. "2011", "2023"
    jurisdiction: str = ""  # optional jurisdiction, e.g. "EU", "US", "INTL"
    reason_text: str = ""   # optional concise human-readable reason summary


# =============================================================================
# INDEX
# =============================================================================

_INDEX: Dict[str, RegulatoryRef] = {}


def _r(
    rule_id: str,
    standard: str,
    document: str,
    section: str,
    obligation: str,
    description: str,
    citation: str = "",
    raw_source_file: str = "",
    *,
    version: str = "",
    jurisdiction: str = "",
    reason_text: str = "",
) -> None:
    # Infer version year from document string if not provided
    if not version:
        for token in document.split():
            if token.isdigit() and len(token) == 4:
                version = token
                break

    # Infer jurisdiction from standard/document if not provided
    if not jurisdiction:
        std_upper = standard.upper()
        if std_upper.startswith("EU"):
            jurisdiction = "EU"
        elif std_upper.startswith("OSHA"):
            jurisdiction = "US"
        else:
            jurisdiction = "INTL"

    _INDEX[rule_id] = RegulatoryRef(
        rule_id=rule_id,
        standard=standard,
        document=document,
        section=section,
        obligation=obligation,
        description=description,
        citation=citation,
        raw_source_file=raw_source_file,
        version=version,
        jurisdiction=jurisdiction,
        reason_text=reason_text or description,
    )


# ── ISO-10218-1:2011 ──────────────────────────────────────────────────────

_r("ISO-GUARD-ZONE-STOP",
   "ISO", "ISO-10218-1:2011", "5.2.1", "mandatory",
   "Movable guards shall be interlocked with hazardous movements so that "
   "the hazardous machine functions cease before they can be reached.",
   raw_source_file="ISO-10218-1-2011.pdf")

_r("ISO-POWER-RESTORE-STATIC",
   "ISO", "ISO-10218-1:2011", "5.2.2", "mandatory",
   "Re-initiation of power shall not lead to any motion.",
   raw_source_file="ISO-10218-1-2011.pdf")

_r("ISO-LOCAL-CONTROL-INHIBIT",
   "ISO", "ISO-10218-1:2011", "5.3.5", "mandatory",
   "When the robot is placed under local pendant control, initiation of robot "
   "motion from any other source is prevented.",
   raw_source_file="ISO-10218-1-2011.pdf")

_r("ISO-SAFETY-CTRL-FAILURE",
   "ISO", "ISO-10218-1:2011", "5.4.1", "mandatory",
   "Any failure of the safety-related control system shall result in a stop "
   "category 0 or 1 in accordance with IEC 60204-1.",
   raw_source_file="ISO-10218-1-2011.pdf")

_r("ISO-SINGLE-FAULT-SAFE-STATE",
   "ISO", "ISO-10218-1:2011", "5.4.2", "mandatory",
   "When the single fault occurs, the safety function is always performed "
   "and a safe state shall be maintained until the detected fault is corrected.",
   raw_source_file="ISO-10218-1-2011.pdf")

_r("ISO-MIN-SEPARATION",
   "ISO", "ISO-10218-1:2011 + ISO/TS 15066:2016", "5.5.1", "mandatory",
   "Every robot shall have a protective stop function. Minimum safe separation "
   "distance shall be maintained between robot and human.",
   raw_source_file="ISO-10218-1-2011.pdf")

_r("ISO-HRI-RESTART-INHIBIT",
   "ISO", "Collaborating robots.pdf", "SC-HRI-03", "mandatory",
   "An attempt to restart motion while the human is still within the "
   "restricted safety zone shall be inhibited.",
   raw_source_file="Collaborating robots.pdf")

_r("ISO-CONTACT-TORQUE-STOP",
   "ISO", "6789.pdf (HRI incident)", "SC-HRI-05", "mandatory",
   "Physical contact detected via joint torque threshold exceeded — "
   "robot must immediately stop and lock restart.",
   raw_source_file="6789.pdf")

_r("ISO-SAFE-ZONE-BOUNDS",
   "ISO", "6789.pdf (HRI incident)", "SC-HRI-06", "mandatory",
   "A control logic error instructing the robot to move outside predefined "
   "safety zones must result in a hard stop requiring technician recovery.",
   raw_source_file="6789.pdf")

# ── OSHA Warehouse ────────────────────────────────────────────────────────

_r("OSHA-SPEED-LIMIT",
   "OSHA", "OSHA_Warehouse.pdf", "Page 3, Forklifts", "mandatory",
   "Never exceed 2.2 m/s (5 mph) for industrial motorized vehicles in "
   "warehouse areas.",
   citation="29 CFR 1910.178(n)(8)",
   raw_source_file="OSHA_Warehouse.pdf")

_r("OSHA-ELEVATED-EDGE",
   "OSHA", "OSHA_Warehouse.pdf", "Page 10, Forklifts", "mandatory",
   "Drivers must not approach the edge of elevated platforms or docks "
   "without maintaining a safe clearance distance.",
   raw_source_file="OSHA_Warehouse.pdf")

_r("OSHA-PINCH-POINT",
   "OSHA", "OSHA_Warehouse.pdf", "Page 4, Forklifts", "mandatory",
   "Driving toward a person standing in front of a fixed object is "
   "prohibited (crush/pin hazard).",
   raw_source_file="OSHA_Warehouse.pdf")

_r("OSHA-VISIBILITY",
   "OSHA", "OSHA_Warehouse.pdf", "Page 10, Forklifts", "mandatory",
   "Drivers are required to look in the direction of and keep a clear "
   "view of the path of travel.",
   raw_source_file="OSHA_Warehouse.pdf")

_r("OSHA-CHARGING-LOCK",
   "OSHA", "OSHA_Warehouse.pdf", "Page 6, Charging Stations", "mandatory",
   "Robot must not move while connected to the charger; braking is "
   "required during battery service.",
   raw_source_file="OSHA_Warehouse.pdf")

_r("OSHA-LOAD-CAPACITY",
   "OSHA", "osha2236.pdf", "Page 7, Mechanical Handling", "mandatory",
   "Handling a load that exceeds the rated lifting/carrying capacity "
   "is prohibited.",
   raw_source_file="osha2236.pdf")

_r("OSHA-LOAD-ELEVATED-TRANSIT",
   "OSHA", "osha2236.pdf", "Page 8, Stacking and Moving", "mandatory",
   "Adjust the load to the lowest position when traveling; elevated "
   "loads during transit significantly increase tipping risk.",
   raw_source_file="osha2236.pdf")

_r("OSHA-AISLE-CLEARANCE",
   "OSHA", "osha2236.pdf", "Page 21, Aisles and Passageways", "mandatory",
   "Entering a path where dynamic or static clearance is insufficient "
   "for the robot and its current load is prohibited.",
   raw_source_file="osha2236.pdf")

# ── OSHA Emergency rules (duplicated from warehouse_safety.yaml) ──────────

_r("EMRG-ESTOP",
   "OSHA+ISO", "OSHA_Warehouse.pdf + ISO-10218-1:2011",
   "General safety", "mandatory",
   "Emergency stop signal — all motion shall immediately cease.",
   raw_source_file="OSHA_Warehouse.pdf")

_r("EMRG-CRITICAL-BATTERY",
   "Hardware+OSHA", "Unitree H1 manual + osha2236.pdf",
   "Battery safety", "mandatory",
   "Battery <5%: robot must dock immediately to prevent mid-task shutdown.",
   raw_source_file="unitree_hardware/")

_r("EMRG-SENSOR-FAILURE",
   "Incident", "Robot doesn't avoid for moving person #8.pdf",
   "Incident #8 root cause", "mandatory",
   "Sensor tracking error reproduces Incident #8 avoidance failure; "
   "robot must stop immediately.",
   raw_source_file="incidents/Robot doesnt avoid for moving person #8.pdf")

# ── HRI incidents ─────────────────────────────────────────────────────────

_r("HRI-PERCEPTION-MISMATCH-STOP",
   "HRI-incident",
   "Robot doesn't avoid for moving person #8.pdf",
   "SC-HRI-01", "mandatory",
   "Human in path + perception/tracking mismatch → robot failed to avoid; "
   "immediate stop required.",
   raw_source_file="incidents/Robot doesnt avoid for moving person #8.pdf")

_r("HRI-SHARED-OBJECT-WAIT",
   "HRI", "Collaborating robots.pdf", "SC-HRI-02", "mandatory",
   "Both robot and human attempting to access same workpiece simultaneously "
   "— robot must yield and wait.",
   raw_source_file="Collaborating robots.pdf")

_r("HRI-DYNAMIC-SAFETY-STOP",
   "HRI", "Collaborating robots.pdf", "SC-HRI-04", "mandatory",
   "Human acceleration toward robot makes static safety buffer insufficient; "
   "dynamic braking and protective stop required.",
   raw_source_file="Collaborating robots.pdf")

# ── Nav2 ─────────────────────────────────────────────────────────────────

_r("NAV-CTRL-SERVER-DOWN",
   "Nav2",
   "First-Time Robot Setup Guide — Nav2 1.0.0 documentation6.pdf",
   "NAV_SCENARIO_07", "mandatory",
   "Nav2 lifecycle manager detects controller server crash → safety stop.",
   raw_source_file="nav2_route_1.pdf")

_r("NAV-LOGIC-DEADLOCK",
   "Operational", "Operational rules", "R-08", "mandatory",
   "Robot stuck in logic deadlock loop → all autonomous actions blocked; "
   "human intervention required.",
   raw_source_file="")

_r("NAV-FAKE-OBSTACLE-RECOVERY",
   "Nav2",
   "Navigation Concepts — Nav2 1.0.0 documentation4.pdf",
   "NAV_SCENARIO_01", "recommended",
   "Costmap full of phantom obstacles → clear_costmap recovery before replanning.",
   raw_source_file="Navigation Concepts — Nav2 1.0.0 documentation4.pdf")

_r("NAV-STUCK-REPLAN",
   "Nav2",
   "Navigation Concepts — Nav2 1.0.0 documentation4.pdf",
   "NAV_SCENARIO_02", "recommended",
   "Robot physically stuck → recovery (backup or spin) before replanning.",
   raw_source_file="Navigation Concepts — Nav2 1.0.0 documentation4.pdf")

_r("NAV-REPEATED-BLOCKED-EDGE",
   "Nav2", "nav2_route_1.pdf", "NAV_SCENARIO_03", "recommended",
   "Route edge blocked repeatedly above frequency threshold → alternate route.",
   raw_source_file="nav2_route_1.pdf")

# ── Hardware ─────────────────────────────────────────────────────────────

_r("HW-LOW-BATTERY-AUTO-CROUCH",
   "Hardware", "Unitree H1 manual", "Battery behavior", "mandatory",
   "Battery <10%: H1 auto-crouches within 10 min (yellow flashing); "
   "must seek charger before this event.",
   raw_source_file="unitree_hardware/")

_r("HW-DAMPING-MODE-OVERRIDE",
   "Hardware", "Unitree Go2/H1 manual", "Hardware modes", "mandatory",
   "damping_mode, locking_posture, and rocker_manual_mode all override "
   "autonomous motion commands.",
   raw_source_file="unitree_hardware/")

_r("HW-MAX-SPEED-ADVISORY",
   "Hardware+TrustLayer",
   "Unitree H1 manual + trust-layer mode_controller",
   "Advisory mode", "mandatory",
   "Trust-layer advisory mode enforces 1.5 m/s speed cap (command_clamp.py).",
   raw_source_file="")

_r("HW-OPERATING-TEMPERATURE",
   "Hardware", "Unitree Go2/H1 manual",
   "Environmental specifications", "mandatory",
   "Operating temperature: 5-35°C; outside range causes hardware degradation.",
   raw_source_file="unitree_hardware/")

# ── ISO 3691-4:2023 (AMR/AGV) ────────────────────────────────────────────

_r("ISO3691-4-ESTOP-001",
   "ISO", "ISO 3691-4:2023", "4.4.1", "mandatory",
   "Emergency stop input active — all motion shall cease before hazards are reached.",
   raw_source_file="ISO-3691-4-2023.pdf")

_r("ISO3691-4-SAFETY-FAULT-001",
   "ISO", "ISO 3691-4:2023", "4.4.1", "mandatory",
   "Failure of safety-related parts of control system requires protective stop.",
   raw_source_file="ISO-3691-4-2023.pdf")

_r("ISO3691-4-SPEED-OPERATING-001",
   "ISO", "ISO 3691-4:2023", "4.5.2", "mandatory",
   "Maximum speed in operating zones where personnel may be present is limited.",
   raw_source_file="ISO-3691-4-2023.pdf")

_r("ISO3691-4-SPEED-TRANSITION-001",
   "ISO", "ISO 3691-4:2023", "4.5.2", "mandatory",
   "Reduced speed in transition zones between restricted and operating areas.",
   raw_source_file="ISO-3691-4-2023.pdf")

_r("ISO3691-4-OVERSPEED-001",
   "ISO", "ISO 3691-4:2023", "4.5.3", "mandatory",
   "Overspeed detection above rated speed shall trigger a protective stop.",
   raw_source_file="ISO-3691-4-2023.pdf")

_r("ISO3691-4-STABILITY-TILT-001",
   "ISO", "ISO 3691-4:2023", "4.3", "mandatory",
   "Truck shall remain stable within specified lateral tilt limits.",
   raw_source_file="ISO-3691-4-2023.pdf")

_r("ISO3691-4-ZONE-RESTRICTED-001",
   "ISO", "ISO 3691-4:2023", "4.2", "mandatory",
   "Restricted zones shall not be entered when personnel may be present.",
   raw_source_file="ISO-3691-4-2023.pdf")

_r("ISO3691-4-MAINTENANCE-001",
   "ISO", "ISO 3691-4:2023", "4.9", "recommended",
   "Periodic maintenance intervals shall be respected and exceeded hours flagged.",
   raw_source_file="ISO-3691-4-2023.pdf")

_r("ISO3691-4-PERSONNEL-STOP-001",
   "ISO", "ISO 3691-4:2023", "4.6", "mandatory",
   "Personnel detected within braking distance in the operating zone shall trigger a protective stop.",
   raw_source_file="ISO-3691-4-2023.pdf")

_r("ISO3691-4-OBSTACLE-001",
   "ISO", "ISO 3691-4:2023", "4.7", "mandatory",
   "Obstacle detected in the forward path within braking distance shall prevent further motion.",
   raw_source_file="ISO-3691-4-2023.pdf")

_r("ISO3691-4-CHARGE-SAFETY-001",
   "ISO", "ISO 3691-4:2023", "4.8", "mandatory",
   "Automatic charging operations shall prevent the truck from moving while connected.",
   raw_source_file="ISO-3691-4-2023.pdf")

# ── ISO 10218:2025 (Collaborative additions, partial) ────────────────────

_r("ISO10218-COLLAB-SPEED-001",
   "ISO", "ISO 10218-2:2025", "11.6", "mandatory",
   "Collaborative operations require reduced speed ≤250 mm/s.",
   raw_source_file="ISO-10218-2-2025.pdf")

_r("ISO10218-STANDSTILL-001",
   "ISO", "ISO 10218-1:2025", "5.4", "mandatory",
   "Monitored standstill must be maintained while human is in collaborative space.",
   raw_source_file="ISO-10218-1-2025.pdf")

_r("ISO10218-FORCE-001",
   "ISO", "ISO 10218-2:2025", "11.6.5", "mandatory",
   "Power and force limiting: contact force must not exceed body-region thresholds.",
   raw_source_file="ISO-10218-2-2025.pdf")

# ── ISO 13482:2014 (personal care robots, partial) ───────────────────────

_r("ISO13482-CONTACT-FORCE-001",
   "ISO", "ISO 13482:2014", "5.5", "mandatory",
   "Contact force for personal care robot must remain below defined thresholds.",
   raw_source_file="ISO-13482-2014.pdf")

_r("ISO13482-SPEED-NEAR-HUMAN-001",
   "ISO", "ISO 13482:2014", "5.3", "recommended",
   "Reduced speed is recommended near assisted person for comfort and safety.",
   raw_source_file="ISO-13482-2014.pdf")

# ── ISO 12100:2010 (framework, partial) ──────────────────────────────────

_r("ISO12100-RISK-ASSESSMENT-001",
   "ISO", "ISO 12100:2010", "4.1", "mandatory",
   "Risk assessment shall be performed for machinery before putting into service.",
   raw_source_file="ISO-12100-2010.pdf")

# ── EU Machinery Regulation 2023/1230 (partial) ────────────────────────────

_r("EU-MR-LOGGING-001",
   "EU", "EU Machinery Regulation 2023/1230", "Annex III 1.1.9", "mandatory",
   "Machinery with safety functions depending on software shall log safety-relevant events.",
   raw_source_file="EU-MR-2023-1230.pdf")

_r("EU-MR-SUPERVISOR-MONITOR-001",
   "EU", "EU Machinery Regulation 2023/1230", "Annex III 1.1.9", "mandatory",
   "Safety-related failures shall be detectable by a human supervisor and trigger alerts.",
   raw_source_file="EU-MR-2023-1230.pdf")

_r("EU-MR-CYBERSECURITY-001",
   "EU", "EU Machinery Regulation 2023/1230", "Annex III 1.1.9", "mandatory",
   "Protection against corruption of safety-related data and software shall be provided.",
   raw_source_file="EU-MR-2023-1230.pdf")

_r("EU-MR-HRI-PREDICTABLE-001",
   "EU", "EU Machinery Regulation 2023/1230", "Annex III 1.1.2", "mandatory",
   "Behaviour of machinery shall be predictable and consistent for persons interacting with it.",
   raw_source_file="EU-MR-2023-1230.pdf")

_r("EU-MR-PSYCHOLOGICAL-STRESS-001",
   "EU", "EU Machinery Regulation 2023/1230", "Annex III 1.1.2", "recommended",
   "Machinery shall be designed so as not to cause unnecessary psychological stress or intimidation.",
   raw_source_file="EU-MR-2023-1230.pdf")

# ── IEC 62443 (cybersecurity, partial) ─────────────────────────────────────

_r("IEC62443-INTEGRITY-001",
   "IEC", "IEC 62443", "TBD", "mandatory",
   "Integrity failure of safety-related control logic requires blocking hazardous motion.",
   raw_source_file="IEC-62443.pdf")

_r("IEC62443-AUTH-001",
   "IEC", "IEC 62443", "TBD", "mandatory",
   "Control channels for issuing motion commands shall be authenticated and secure.",
   raw_source_file="IEC-62443.pdf")

_r("IEC62443-UPDATE-001",
   "IEC", "IEC 62443", "TBD", "mandatory",
   "Safety-related software updates shall be verified before returning to operation.",
   raw_source_file="IEC-62443.pdf")

_r("ISO12100-INHERENT-SAFETY-001",
   "ISO", "ISO 12100:2010", "6.2", "recommended",
   "Inherently safe design measures should be preferred over safeguarding and information for use.",
   raw_source_file="ISO-12100-2010.pdf")

# ── ISO 13849-1:2023 (performance levels, partial) ───────────────────────

_r("ISO13849-PLD-PERSONNEL-DETECTION-001",
   "ISO", "ISO 13849-1:2023", "6.2", "mandatory",
   "Safety functions for personnel detection shall achieve at least PL d.",
   raw_source_file="ISO-13849-1-2023.pdf")

_r("ISO13849-DC-DIAGNOSTIC-001",
   "ISO", "ISO 13849-1:2023", "7.3", "recommended",
   "Diagnostic coverage should be ensured and documented for safety-related parts.",
   raw_source_file="ISO-13849-1-2023.pdf")

# ── EU Machinery Regulation 2023/1230 (partial) ──────────────────────────

_r("EU-MR-AI-LOG-001",
   "EU-MR", "EU Machinery Regulation 2023/1230", "Annex III 1.2.1", "mandatory",
   "Safety-related decisions must be logged each time a decision is taken.",
   raw_source_file="EU-MR-2023-1230.pdf")

_r("EU-MR-AMR-SUPERVISOR-001",
   "EU-MR", "EU Machinery Regulation 2023/1230", "Annex III 1.2.1", "mandatory",
   "Autonomous mobile machinery must provide real-time information and allow intervention by a human supervisor.",
   raw_source_file="EU-MR-2023-1230.pdf")

_r("EU-MR-CYBER-001",
   "EU-MR", "EU Machinery Regulation 2023/1230", "Annex III 1.1.9", "mandatory",
   "Safety functions shall be protected against corruption and external interference.",
   raw_source_file="EU-MR-2023-1230.pdf")

_r("EU-MR-HRI-PREDICTABLE-001",
   "EU-MR", "EU Machinery Regulation 2023/1230", "Annex III 1.2.1", "recommended",
   "Robot actions should be predictable and understandable to operators.",
   raw_source_file="EU-MR-2023-1230.pdf")

# ── EU AI Act 2024/1689 (partial, advisory) ──────────────────────────────

_r("EU-AIACT-RM-001",
   "EU-AI-Act", "EU AI Act 2024/1689", "Article 9", "informational",
   "High-risk AI systems shall have a documented, ongoing risk management system.",
   raw_source_file="EU-AI-Act-2024-1689.pdf")

_r("EU-AIACT-LOG-001",
   "EU-AI-Act", "EU AI Act 2024/1689", "Article 12", "informational",
   "High-risk AI systems shall be designed with logging capabilities.",
   raw_source_file="EU-AI-Act-2024-1689.pdf")

_r("EU-AIACT-HUMAN-OVERSIGHT-001",
   "EU-AI-Act", "EU AI Act 2024/1689", "Article 14", "informational",
   "High-risk AI systems require appropriate human oversight enabling understanding, monitoring, and override.",
   raw_source_file="EU-AI-Act-2024-1689.pdf")

# ── ISO 3691-4:2023 — additional rules ─────────────────────────────────

_r("ISO3691-4-PERSONNEL-SLOW-001",
   "ISO", "ISO 3691-4:2023", "4.6", "mandatory",
   "Person in warning zone (>braking dist, <5m) — speed reduced to ≤0.3 m/s.")

_r("ISO3691-4-PERSONNEL-DETECT-FAIL-001",
   "ISO", "ISO 3691-4:2023", "4.6", "mandatory",
   "Personnel detection system failure — protective stop required.")

_r("ISO3691-4-PERSONNEL-REVERSE-001",
   "ISO", "ISO 3691-4:2023", "4.6", "mandatory",
   "Reverse travel requires rear personnel detection with same reliability as forward.")

_r("ISO3691-4-PERSONNEL-SIDE-001",
   "ISO", "ISO 3691-4:2023", "4.6", "mandatory",
   "Lateral movement requires side personnel detection active.")

_r("ISO3691-4-PERSONNEL-HYPOTHESIS-001",
   "ISO", "ISO 3691-4:2023", "4.6", "mandatory",
   "Entity with status=hypothesis and semantic_type=human treated as real person.")

_r("ISO3691-4-OBSTACLE-FORWARD-001",
   "ISO", "ISO 3691-4:2023", "4.7", "mandatory",
   "Forward detection zone depth must be ≥ braking distance + margin.")

_r("ISO3691-4-OBSTACLE-UNKNOWN-001",
   "ISO", "ISO 3691-4:2023", "4.7", "mandatory",
   "Unknown object in path — limit speed to 0.3 m/s and notify operator.")

_r("ISO3691-4-OBSTACLE-CLEARANCE-001",
   "ISO", "ISO 3691-4:2023", "4.7", "mandatory",
   "Minimum clearance of 0.5m required when passing obstacles.")

_r("ISO3691-4-OBSTACLE-SENSOR-RANGE-001",
   "ISO", "ISO 3691-4:2023", "4.7", "mandatory",
   "If sensor range < required detection distance for current speed — reduce speed.")

_r("ISO3691-4-OBSTACLE-LOWCONF-001",
   "ISO", "ISO 3691-4:2023", "4.7", "recommended",
   "Obstacle with confidence 0.15–0.5 in path treated as real (conservative).")

_r("ISO3691-4-CHARGE-ZONE-001",
   "ISO", "ISO 3691-4:2023", "4.8", "mandatory",
   "In charging zone speed must not exceed 0.1 m/s.")

_r("ISO3691-4-CHARGE-PERSONNEL-001",
   "ISO", "ISO 3691-4:2023", "4.8", "mandatory",
   "Person detected in charging zone — interrupt charging and stop.")

_r("ISO3691-4-CHARGE-VOLTAGE-001",
   "ISO", "ISO 3691-4:2023", "4.8", "mandatory",
   "Charging contacts energized only when properly connected to charger.")

_r("ISO3691-4-CHARGE-FAULT-001",
   "ISO", "ISO 3691-4:2023", "4.8", "recommended",
   "Charging system fault — disconnect and notify operator.")

_r("ISO3691-4-BRAKE-TEST-001",
   "ISO", "ISO 3691-4:2023", "4.4.2", "recommended",
   "Periodic braking system test shall be scheduled and completed.")

_r("ISO3691-4-LOAD-STABILITY-001",
   "ISO", "ISO 3691-4:2023", "4.3", "recommended",
   "When carrying load, speed and tilt limits are tightened for stability.")

_r("ISO3691-4-MANUAL-MODE-001",
   "ISO", "ISO 3691-4:2023", "4.5.1", "mandatory",
   "In manual mode, maximum speed shall not exceed rated manual speed.")

# ── ISO 10218:2025 — additional collaborative rules ────────────────────

_r("ISO10218-PFL-HEAD-001",
   "ISO", "ISO 10218-2:2025", "11.6.5", "mandatory",
   "Head contact: max 130N transient, 65N quasi-static.")

_r("ISO10218-PFL-CHEST-001",
   "ISO", "ISO 10218-2:2025", "11.6.5", "mandatory",
   "Chest contact: max 150N transient, 140N quasi-static.")

_r("ISO10218-PFL-HAND-001",
   "ISO", "ISO 10218-2:2025", "11.6.5", "mandatory",
   "Hand/arm contact: max 235N transient, 140N quasi-static.")

_r("ISO10218-SSM-001",
   "ISO", "ISO 10218-2:2025", "11.6.4", "mandatory",
   "SSM protective distance violated — robot must stop.")

_r("ISO10218-SSM-SPEED-001",
   "ISO", "ISO 10218-2:2025", "11.6.4", "mandatory",
   "In SSM mode, robot speed must be ≤ f(separation_distance).")

_r("ISO10218-CYBER-001",
   "ISO", "ISO 10218-1:2025", "5.6", "recommended",
   "Cybersecurity measures for safety-related control systems.")

_r("ISO10218-WORKSPACE-001",
   "ISO", "ISO 10218-2:2025", "11.6.2", "mandatory",
   "Collaborative workspace must have clearly defined boundaries.")

_r("ISO10218-SAFEGUARD-RESET-001",
   "ISO", "ISO 10218-1:2025", "5.4", "mandatory",
   "After protective stop, manual reset required before resuming motion.")

# ── ISO 13482:2014 — additional rules ──────────────────────────────────

_r("ISO13482-STABILITY-001",
   "ISO", "ISO 13482:2014", "5.4", "mandatory",
   "Robot must not tip over during normal use.")

_r("ISO13482-SPEED-LIMIT-001",
   "ISO", "ISO 13482:2014", "5.3", "mandatory",
   "Speed limit for personal care environment.")

_r("ISO13482-NOISE-001",
   "ISO", "ISO 13482:2014", "5.8", "recommended",
   "Noise levels should not exceed comfort threshold.")

_r("ISO13482-ENTRAPMENT-001",
   "ISO", "ISO 13482:2014", "5.6", "mandatory",
   "Entrapment of body parts must be prevented.")

# ── ISO 12100:2010 — additional rules ──────────────────────────────────

_r("ISO12100-HIERARCHY-001",
   "ISO", "ISO 12100:2010", "6.1", "recommended",
   "Protective measures hierarchy: inherent safe → safeguarding → information.")

_r("ISO12100-MECH-CRUSH-001",
   "ISO", "ISO 12100:2010", "6.2.2", "mandatory",
   "Protection against crushing hazard required.")

_r("ISO12100-MECH-SHEAR-001",
   "ISO", "ISO 12100:2010", "6.2.2", "mandatory",
   "Protection against shearing hazard required.")

_r("ISO12100-MECH-ENTANGLE-001",
   "ISO", "ISO 12100:2010", "6.2.2", "mandatory",
   "Protection against entanglement required.")

# ── ISO 13849-1:2023 — additional rules ────────────────────────────────

_r("ISO13849-PLC-ESTOP-001",
   "ISO", "ISO 13849-1:2023", "6.2", "mandatory",
   "Emergency stop function requires PL e.")

_r("ISO13849-DIAG-001",
   "ISO", "ISO 13849-1:2023", "7.3", "recommended",
   "Diagnostic coverage monitoring for safety functions.")

_r("ISO13849-CCF-001",
   "ISO", "ISO 13849-1:2023", "7.4", "recommended",
   "Common cause failure protection measures.")

_r("ISO13849-MTTR-001",
   "ISO", "ISO 13849-1:2023", "7.2", "recommended",
   "Mean time to dangerous failure (MTTFd) tracking.")

# ── EU Machinery Regulation 2023/1230 — additional rules ───────────────

_r("EU-MR-AI-EVOLVING-001",
   "EU-MR", "EU Machinery Regulation 2023/1230", "Annex III 1.1.2", "mandatory",
   "Risk assessment required for evolving/self-learning AI logic.")

_r("EU-MR-SW-UPDATE-001",
   "EU-MR", "EU Machinery Regulation 2023/1230", "Annex III 1.1.9", "mandatory",
   "Safety-affecting software update requires reassessment.")

_r("EU-MR-CONTROL-FAILURE-001",
   "EU-MR", "EU Machinery Regulation 2023/1230", "Annex III 1.2.1", "mandatory",
   "Control system failure must not lead to hazardous situation.")

_r("EU-MR-ENERGY-CUTOFF-001",
   "EU-MR", "EU Machinery Regulation 2023/1230", "Annex III 1.6.3", "mandatory",
   "Possibility to disconnect from all energy sources must be provided.")

_r("EU-MR-EMERGENCY-STOP-001",
   "EU-MR", "EU Machinery Regulation 2023/1230", "Annex III 1.2.4.3", "mandatory",
   "Emergency stop device must be ISO 13850 compliant.")

_r("EU-MR-STARTUP-001",
   "EU-MR", "EU Machinery Regulation 2023/1230", "Annex III 1.2.3", "mandatory",
   "Unexpected startup must be prevented.")

_r("EU-MR-COMM-FAILURE-001",
   "EU-MR", "EU Machinery Regulation 2023/1230", "Annex III 1.2.1", "mandatory",
   "Communication failure must lead to safe state.")

_r("EU-MR-INSTRUCTIONS-001",
   "EU-MR", "EU Machinery Regulation 2023/1230", "Annex III 1.7.4", "recommended",
   "Instructions should be available in digital format.")

_r("EU-MR-MARKING-001",
   "EU-MR", "EU Machinery Regulation 2023/1230", "Article 21", "recommended",
   "CE marking requirements must be met.")

# ── EU AI Act 2024/1689 — additional rules ─────────────────────────────

_r("EUAI-RISK-MGMT-001",
   "EU-AI-Act", "EU AI Act 2024/1689", "Article 9", "mandatory",
   "Documented risk management system for high-risk AI.")

_r("EUAI-DATA-GOV-001",
   "EU-AI-Act", "EU AI Act 2024/1689", "Article 10", "recommended",
   "Training data governance documentation.")

_r("EUAI-TRANSPARENCY-001",
   "EU-AI-Act", "EU AI Act 2024/1689", "Article 13", "recommended",
   "Deployers informed about AI capabilities and limitations.")

_r("EUAI-ACCURACY-001",
   "EU-AI-Act", "EU AI Act 2024/1689", "Article 15", "mandatory",
   "AI accuracy, robustness, and cybersecurity validated.")

# ── IEC 62443 — additional rules ───────────────────────────────────────

_r("IEC62443-NETWORK-001",
   "IEC", "IEC 62443-3-3", "SR 5.1", "mandatory",
   "OT/IT network segmentation required.")

_r("IEC62443-AUDIT-LOG-001",
   "IEC", "IEC 62443-3-3", "SR 6.1", "mandatory",
   "Security event logging required.")

_r("IEC62443-PATCH-001",
   "IEC", "IEC 62443-2-3", "SR 7.6", "recommended",
   "Security patch management process required.")


# =============================================================================
# PUBLIC API
# =============================================================================

class RegulatoryIndex:
    """Lookup table: rule_id → RegulatoryRef."""

    def __init__(self, index: Optional[Dict[str, RegulatoryRef]] = None) -> None:
        self._index = index or _INDEX

    def lookup(self, rule_id: str) -> Optional[RegulatoryRef]:
        """Return RegulatoryRef for rule_id, or None if not indexed."""
        return self._index.get(rule_id)

    def by_standard(self, standard: str) -> List[RegulatoryRef]:
        """Return all rules from a given standard (ISO | OSHA | HRI | Nav2 | Hardware)."""
        return [r for r in self._index.values()
                if r.standard.upper().startswith(standard.upper())]

    def by_obligation(self, obligation: str) -> List[RegulatoryRef]:
        """Return rules with given obligation level (mandatory | recommended)."""
        return [r for r in self._index.values()
                if r.obligation == obligation]

    def all_rule_ids(self) -> List[str]:
        return list(self._index.keys())

    def summary(self) -> Dict[str, int]:
        """Count of rules per standard."""
        counts: Dict[str, int] = {}
        for ref in self._index.values():
            k = ref.standard.split("+")[0].strip()
            counts[k] = counts.get(k, 0) + 1
        return counts


# Module-level singleton
_default_index = RegulatoryIndex()


def lookup(rule_id: str) -> Optional[RegulatoryRef]:
    """Shortcut: look up rule_id in the default index."""
    return _default_index.lookup(rule_id)
