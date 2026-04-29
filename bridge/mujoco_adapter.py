"""MuJoCo adapter — proxies the generic Trust Layer bridge API to a
running `mujoco_bridge` HTTP server (Unitree G1 + dexterous hands in
MuJoCo physics).

Why this exists: we want the exact same code path — chat →
nl_command_gateway → task_executor → robot_bridge → adapter — to drive
both a real Noetix / Unitree robot AND our MuJoCo simulation. No
parallel "demo shortcut". Every rule in the safety pipeline, every
capability token, every audit entry runs on the simulated run too.

The upstream mujoco_bridge API surface (see
services/mujoco_bridge/main.py in the trust-layer repo) is richer than
what the generic adapter interface exposes:

    /health                 — physics + render status
    /robot/state            — position, heading, joints, capabilities
    /robot/move             — {vx, vy, wz} velocity command
    /robot/move_to          — {x, y} absolute target
    /robot/move_relative    — {dx, dy, dyaw_deg} in robot frame
    /robot/rotate           — {angle_deg, mode} body yaw
    /robot/stop             — emergency stop
    /robot/head             — {pan_deg, tilt_deg, roll_deg}
    /robot/look_around      — canned pan sweep (returns observations)
    /robot/find             — rotate-and-scan for named target
    /robot/pick             — pick state machine
    /robot/place            — place state machine
    /gesture                — wave / nod / shake / cheer
    /camera/rgb             — head-cam JPEG
    /camera/depth           — float32 depth buffer
    /sensors/lidar          — 2D ray sweep
    /sensors/imu            — gyro + accelerometer
    /scene/objects          — ground-truth scene state
    /scene/describe         — grounded text description (EN + RU)
    /look/result            — poll look_around progress
    /find/result            — poll find progress

We expose them through the generic adapter methods where a 1:1 mapping
exists, and via `handle_action` for the richer primitives (look_around,
find, describe_scene, head_pan/tilt, gestures). `handle_action` is what
`robot_bridge/main.py` already calls for `ros2_publish` and gesture
actions from the gateway, so we're extending a pattern — not inventing
one.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

from bridge.adapter_base import ProbeStatus, RobotAdapter


class MujocoAdapter(RobotAdapter):
    """Generic Trust Layer adapter → MuJoCo robot bridge (HTTP)."""

    def __init__(self, robot_url: str = "http://mujoco_bridge:8000"):
        self.robot_url = robot_url.rstrip("/")
        self.connected = False
        self.name = "mujoco"
        self._last_error = ""
        self._timeout = 3.0
        self._caps_cache: dict | None = None
        # Gesture name cache — populated on first probe_capabilities()
        # or first handle_action("gesture", …) call, used to refuse
        # unsupported names without round-tripping the bridge.
        self._gesture_names_cache: list | None = None

    # ── Telemetry ──────────────────────────────────────────────────────

    def get_state(self) -> dict:
        raw = self._get("/robot/state")
        if raw is None:
            return self._error_state()
        self.connected = True
        pos = raw.get("position") or {}
        # mujoco_bridge reports `speed` / `speed_mps` directly; compose
        # a velocity vector so downstream code that reads `velocity.vx`
        # still works.
        speed = float(raw.get("speed_mps") or raw.get("speed") or 0.0)
        heading = float(raw.get("heading") or 0.0)
        vx = speed * math.cos(heading)
        vy = speed * math.sin(heading)
        return {
            "position": {
                "x": float(pos.get("x", 0.0)),
                "y": float(pos.get("y", 0.0)),
                "z": float(pos.get("z", 0.0)),
            },
            "velocity": {"vx": vx, "vy": vy, "vz": 0.0},
            "heading_rad": heading,
            "heading_deg": float(raw.get("heading_deg") or math.degrees(heading)),
            "speed_mps": speed,
            "battery": float(raw.get("battery") or 95.0),
            "tilt_deg": float(raw.get("tilt_deg") or 0.0),
            "temperature_c": 25.0,
            "mode": raw.get("mode") or "ADVISORY",
            "timestamp_s": time.time(),
            "adapter": self.name,
            # Pass-throughs task_executor uses for motion-detection
            # when base odometry is missing. mujoco_bridge already
            # returns `joint_positions` in the response.
            "joint_positions": [float(j) for j in (raw.get("joint_positions") or [])],
            "joint_names": list(raw.get("joint_names") or []),
            # Rich MuJoCo-only fields forwarded verbatim so the UI /
            # task_executor can surface them when present (action,
            # gesture, task_phase, holding, etc.).
            "action": raw.get("action"),
            "gesture": raw.get("gesture"),
            "task_phase": raw.get("task_phase"),
            "holding": raw.get("holding"),
            "objects": raw.get("objects") or [],
            "head": raw.get("head"),
            "look_phase": raw.get("look_phase"),
            "find_phase": raw.get("find_phase"),
            "find_result": raw.get("find_result"),
        }

    def get_entities(self) -> list[dict]:
        """MuJoCo scene objects reported as Trust Layer entities.

        Humans become `is_human=True, class_name="person"` so the
        ISO 13482 §5.7.2 speed-near-human rule can key on them.
        Cubes → `class_name="cube"`, zones → `class_name="zone"`.
        task_executor uses this list for proximity checks; the robot
        bridge's action_pipeline / HITL escalation reads `is_human`
        + `distance_m` to enforce the speed-near-human envelope.
        """
        scene = self._get("/scene/objects") or {}
        out: list[dict] = []
        state = self._get("/robot/state") or {}
        pos = (state.get("position") or {})
        rx = float(pos.get("x", 0.0))
        ry = float(pos.get("y", 0.0))
        # Humans first — most safety-critical + shortest list.
        for h in scene.get("humans") or []:
            hp = h.get("pos") or {}
            dx = float(hp.get("x", 0.0)) - rx
            dy = float(hp.get("y", 0.0)) - ry
            out.append({
                "entity_id": h.get("id") or "person",
                "class_name": h.get("class_name") or "person",
                "distance_m": round(math.hypot(dx, dy), 2),
                "is_human": True,
                "position": hp,
                "zone": h.get("zone"),
            })
        for obj in scene.get("objects") or []:
            op = obj.get("pos") or {}
            dx = float(op.get("x", 0.0)) - rx
            dy = float(op.get("y", 0.0)) - ry
            out.append({
                "entity_id": obj.get("id"),
                "class_name": "cube",
                "colour": obj.get("colour"),
                "distance_m": round(math.hypot(dx, dy), 2),
                "is_human": False,
                "position": op,
            })
        for zone in scene.get("zones") or []:
            zp = zone.get("pos") or {}
            dx = float(zp.get("x", 0.0)) - rx
            dy = float(zp.get("y", 0.0)) - ry
            out.append({
                "entity_id": f"zone_{zone.get('label')}",
                "class_name": "zone",
                "distance_m": round(math.hypot(dx, dy), 2),
                "is_human": False,
                "position": zp,
            })
        return out

    # ── Actuation ──────────────────────────────────────────────────────

    def send_velocity(self, vx: float, vy: float, wz: float) -> dict:
        result = self._post("/robot/move", {"vx": vx, "vy": vy, "wz": wz})
        if result is not None:
            return {"status": "ok", "adapter": self.name}
        return {"status": "error", "error": self._last_error}

    def navigate_to(self, x_m: float, y_m: float,
                    heading_rad: float = 0.0,
                    speed_mps: float = 0.3) -> dict:
        """Absolute (x, y) target — mujoco_bridge has native support
        via /robot/move_to; the kinematic drive handles pacing at the
        bridge's configured max speed."""
        result = self._post("/robot/move_to", {"x": x_m, "y": y_m})
        if result is None:
            return {"status": "error", "error": self._last_error}
        return {
            "status": "moving_to_destination",
            "target": {"x": x_m, "y": y_m},
            "adapter": self.name,
            **result,
        }

    def stop(self) -> dict:
        result = self._post("/robot/stop", {})
        if result is not None:
            return {"status": "stopped", "adapter": self.name}
        # Last-ditch — zero velocity so the kinematic drive unparks
        # even if /robot/stop itself failed.
        self.send_velocity(0.0, 0.0, 0.0)
        return {"status": "stopped", "adapter": self.name,
                "note": "fallback_zero_velocity"}

    # ── Scenarios ──────────────────────────────────────────────────────

    def inject_scenario(self, overrides: dict) -> None:
        # mujoco_bridge doesn't (yet) support scenario injection — the
        # scene is authoritative ground truth. Log so test cases see
        # the request without raising.
        logger.info("inject_scenario ignored by mujoco adapter: %r",
                    list(overrides.keys()))

    def clear_scenario(self) -> None:
        logger.info("clear_scenario ignored by mujoco adapter")

    # ── Lidar ──────────────────────────────────────────────────────────

    def get_lidar_scan(self) -> dict:
        """Expose the MuJoCo 2D lidar in the schema the generic bridge
        endpoint expects (ranges + angle_min/max/increment)."""
        raw = self._get("/sensors/lidar?rays=72&range=8")
        if raw is None:
            return {
                "available": False,
                "error": self._last_error or "unreachable",
                "adapter": self.name,
            }
        ranges = [r if r is not None else float("inf")
                  for r in (raw.get("ranges") or [])]
        n = len(ranges) or 1
        return {
            "available": True,
            "source": "mujoco.mj_ray",
            "adapter": self.name,
            "ranges": ranges,
            "angle_min_rad": -math.pi,
            "angle_max_rad": math.pi,
            "angle_increment_rad": (2 * math.pi) / n,
            "range_min_m": 0.05,
            "range_max_m": float(raw.get("max_range_m") or 8.0),
            "timestamp_s": time.time(),
        }

    # ── Camera ─────────────────────────────────────────────────────────

    def capture_photo(self) -> dict:
        """Generic bridge endpoint `capture_photo` → MuJoCo head cam.
        Returns a base64-encoded JPEG under both `data` and `image_b64`
        — vlm_processor reads `data`, the look_around / find_visual
        path reads `image_b64`. Carrying both keeps the wire format
        ambiguous-tolerant without adding a translation layer."""
        import base64
        try:
            url = f"{self.robot_url}/camera/rgb"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                jpeg = resp.read()
            b64 = base64.b64encode(jpeg).decode("ascii")
            return {
                "ok": True,
                "adapter": self.name,
                "format": "jpeg",
                "method": "mujoco_render",
                "width": 480,
                "height": 320,
                "data": b64,
                "image_b64": b64,
                "timestamp_s": time.time(),
            }
        except Exception as exc:
            self._last_error = str(exc)
            return {"ok": False, "adapter": self.name,
                    "error": self._last_error}

    # ── Capabilities ──────────────────────────────────────────────────

    def _supported_gestures(self) -> list:
        """List of gesture names this bridge actually implements.

        Reads from the live /robot/state.capability_details. Cached
        after the first successful response so the per-action lookup
        is cheap. Falls back to an empty list if the bridge hasn't
        come up yet — handle_action then refuses every gesture
        loudly with a clear "unsupported" reply rather than silently
        firing into a bridge that won't answer.
        """
        if self._gesture_names_cache is not None:
            return self._gesture_names_cache
        state = self._get("/robot/state") or {}
        details = state.get("capability_details") or {}
        gesture_block = details.get("gesture") or {}
        names = gesture_block.get("names") or []
        if names:
            self._gesture_names_cache = list(names)
        return list(names)

    def probe_capabilities(self) -> dict:
        if self._caps_cache is not None:
            return self._caps_cache
        state = self._get("/robot/state") or {}
        sensors = state.get("sensors_available") or {}
        caps_list = state.get("capabilities") or []
        cap_details = state.get("capability_details") or {}
        connected = bool(state)
        self.connected = connected
        caps = {
            "camera": {
                "available": bool(sensors.get("camera_rgb")),
                "probe": ProbeStatus.OK if sensors.get("camera_rgb") else ProbeStatus.NOT_INSTALLED,
                "note": "MuJoCo head_cam (480×320, 70° FOV)",
            },
            "lidar": {
                "available": bool(sensors.get("lidar_2d")),
                "probe": ProbeStatus.OK if sensors.get("lidar_2d") else ProbeStatus.NOT_INSTALLED,
                "note": "Synthetic 2D via mj_ray (72 beams, 8m)",
            },
            "imu": {
                "available": bool(sensors.get("imu")),
                "probe": ProbeStatus.OK if sensors.get("imu") else ProbeStatus.NOT_INSTALLED,
                "note": "G1 pelvis + torso IMU (gyro + accel)",
            },
            "microphone": {
                "available": False,
                "probe": ProbeStatus.CLIENT_STT,
                "note": "MuJoCo has no mic — client Web Speech API fallback",
            },
            "speaker": {
                "available": False,
                "probe": ProbeStatus.CLIENT_TTS,
                "note": "MuJoCo has no speaker — client Web Speech API fallback",
            },
            "drive": {
                "available": True,
                "probe": ProbeStatus.OK if connected else ProbeStatus.DISCONNECTED,
                "type": "kinematic_legged",
                "max_speed_mps": 0.5,
                "note": "Kinematic root drive, PD-controlled 43-DOF G1 legs",
            },
            "battery": {
                "available": True,
                "level_pct": float(state.get("battery") or 95.0),
                "probe": ProbeStatus.OK,
            },
            "network": {
                "available": connected,
                "probe": ProbeStatus.OK if connected else ProbeStatus.DISCONNECTED,
                "latency_ms": 1.0,
                "adapter": self.name,
            },
            # Extras — not in CAPABILITY_KEYS but preserved by
            # normalize_capabilities(). Surface the richer action set
            # so the UI can show "this robot can scan / find / describe".
            "actions": {
                "available": True,
                "list": caps_list,
                # Pass through the detail block published by the
                # bridge — this is what makes the LLM-side prompt
                # builder capability-driven instead of hardcoded.
                # If a robot doesn't publish capability_details, the
                # block is just empty; nothing breaks.
                "details": cap_details,
            },
            "gestures": {
                "available": bool((cap_details.get("gesture") or {}).get("names")),
                "names": list((cap_details.get("gesture") or {}).get("names") or []),
                "library": dict((cap_details.get("gesture") or {}).get("library") or {}),
            },
            "depth_camera": {
                "available": bool(sensors.get("camera_depth")),
                "probe": ProbeStatus.OK if sensors.get("camera_depth") else ProbeStatus.NOT_INSTALLED,
                "note": "MuJoCo offscreen depth renderer",
            },
        }
        self._caps_cache = caps
        return caps

    # ── Extended action dispatch ──────────────────────────────────────
    # robot_bridge/main.py calls _adapter.handle_action for any
    # action_type that doesn't map to send_velocity / navigate_to /
    # stop / capture_photo. We route those to the richer mujoco_bridge
    # endpoints so task_executor's find_visual, look_around,
    # describe_scene, head_pan, rotate, gestures all work.

    def handle_action(self, action_type: str, params: dict) -> dict:
        try:
            a = (action_type or "").lower()
            p = params or {}

            # ── Gestures ────────────────────────────────────────────
            # Two paths:
            #   1. Canonical intent: action_type="gesture", params={name}
            #   2. Legacy alias:     action_type="wave"|"nod"|...
            # Either way we read the bridge's live gesture library so
            # supported names are sourced from /robot/state, not a
            # hardcoded list. Greet/hello aliases collapse to wave.
            supported_gestures = self._supported_gestures()

            if a == "gesture":
                requested = (p.get("name") or "").strip().lower()
                if not requested:
                    return {"ok": False, "status": "error", "adapter": self.name,
                            "action": "gesture", "error": "missing_name",
                            "supported": supported_gestures}
                if requested == "greet" or requested == "hello":
                    requested = "wave"
                if requested not in supported_gestures:
                    return {"ok": False, "status": "error", "adapter": self.name,
                            "action": "gesture", "error": "unsupported_gesture",
                            "requested": requested,
                            "supported": supported_gestures}
                r = self._post("/gesture", {"name": requested})
                if r is None:
                    return {"ok": False, "status": "error", "adapter": self.name,
                            "action": "gesture", "error": self._last_error or "bridge_unreachable"}
                # Bridge returns {"status":"ok", "gesture", "duration_s", ...}
                # Surface as a uniform success envelope the gateway can read.
                return {"ok": True, "adapter": self.name, "action": "gesture",
                        **r}

            if a in ("wave", "greet", "hello", "nod", "shake", "bow",
                     "clap", "point_forward", "arms_up", "cheer"):
                name = "wave" if a in ("wave", "greet", "hello") else a
                if name not in supported_gestures:
                    return {"ok": False, "status": "error", "adapter": self.name,
                            "action": "gesture", "error": "unsupported_gesture",
                            "requested": name,
                            "supported": supported_gestures}
                r = self._post("/gesture", {"name": name})
                if r is None:
                    return {"ok": False, "status": "error", "adapter": self.name,
                            "action": "gesture", "error": self._last_error or "bridge_unreachable"}
                return {"ok": True, "adapter": self.name, "action": "gesture",
                        **r}

            # Rotation — body yaw, absolute or relative degrees.
            if a in ("rotate", "turn", "spin"):
                angle = float(p.get("angle_deg") or p.get("angle") or 45.0)
                direction = (p.get("direction") or "").lower()
                if direction in ("right", "cw", "направо", "вправо"):
                    angle = -abs(angle)
                elif direction in ("left", "ccw", "налево", "влево"):
                    angle = abs(angle)
                mode = p.get("mode") or "relative"
                r = self._post("/robot/rotate",
                               {"angle_deg": angle, "mode": mode})
                return r or {"status": "error", "error": self._last_error}

            # Relative motion — in robot frame.
            if a in ("move_relative", "move_forward", "move_backward",
                     "step_forward", "step_backward"):
                dx = float(p.get("dx") or p.get("distance_m")
                           or (1.0 if "forward" in a else -1.0))
                if a == "move_backward" or a == "step_backward":
                    dx = -abs(dx)
                r = self._post("/robot/move_relative",
                               {"dx": dx,
                                "dy": float(p.get("dy") or 0.0),
                                "dyaw_deg": float(p.get("dyaw_deg") or 0.0)})
                return r or {"status": "error", "error": self._last_error}

            # Head pan / tilt (the G1 has no neck — mujoco_bridge
            # treats waist_yaw/pitch as "head" so the robot visibly
            # reorients).
            if a in ("head_pan", "head_tilt", "head_roll", "head"):
                pan = p.get("pan_deg") if a == "head_pan" else p.get("pan_deg")
                tilt = p.get("tilt_deg") if a == "head_tilt" else p.get("tilt_deg")
                roll = p.get("roll_deg") if a == "head_roll" else p.get("roll_deg")
                if a == "head_pan" and pan is None:
                    pan = p.get("angle_deg") or p.get("angle") or 0.0
                if a == "head_tilt" and tilt is None:
                    tilt = p.get("angle_deg") or p.get("angle") or 0.0
                if a == "head_roll" and roll is None:
                    roll = p.get("angle_deg") or p.get("angle") or 0.0
                body = {}
                if pan is not None:  body["pan_deg"]  = float(pan)
                if tilt is not None: body["tilt_deg"] = float(tilt)
                if roll is not None: body["roll_deg"] = float(roll)
                r = self._post("/robot/head", body)
                return r or {"status": "error", "error": self._last_error}

            # Perception — look_around with polling so the caller gets
            # observations in one response.
            if a in ("look_around", "look", "scan", "inspect"):
                n = int(p.get("n") or 5)
                span = float(p.get("span_deg") or 120.0)
                if self._post("/robot/look_around",
                              {"n": n, "span_deg": span}) is None:
                    return {"status": "error", "error": self._last_error}
                obs = self._poll("/look/result", timeout_s=n * 1.5 + 3.0) or {}
                desc = self._get("/scene/describe") or {}
                return {
                    "status": "ok", "adapter": self.name,
                    "action": "look_around",
                    "observations": obs.get("observations") or [],
                    "describe": desc,
                }

            if a in ("describe_scene", "describe"):
                desc = self._get("/scene/describe") or {}
                return {"status": "ok", "adapter": self.name,
                        "action": "describe_scene",
                        "describe": desc}

            if a in ("find_visual", "find", "find_object", "search", "locate"):
                target = (p.get("target") or p.get("object") or "").strip()
                if not target:
                    return {"status": "error",
                            "error": "find requires a target"}
                if self._post("/robot/find",
                              {"target": target,
                               "timeout_s": float(p.get("timeout_s") or 6.0)}) is None:
                    return {"status": "error", "error": self._last_error,
                            "reason": "unknown_target"}
                res = self._poll("/find/result", timeout_s=8.0) or {}
                return {"status": "ok", "adapter": self.name,
                        "action": "find_visual",
                        "target": target,
                        "result": res.get("result"),
                        "done": res.get("done", False)}

            # Manipulation — pick/place state machines.
            if a in ("pick", "grasp", "grip"):
                obj_ref = p.get("object") or p.get("target")
                place = (p.get("place") or p.get("place_zone")
                         or p.get("zone") or "")
                body = {"object": obj_ref}
                if place:
                    body["place"] = place
                r = self._post("/robot/pick", body)
                return r or {"status": "error", "error": self._last_error}

            if a in ("place", "drop"):
                zone = (p.get("zone") or p.get("place_zone")
                        or p.get("place") or "").upper()
                r = self._post("/robot/place", {"zone": zone})
                return r or {"status": "error", "error": self._last_error}

            # motor_compiler joint-velocity passthrough. Mujoco bridge
            # has /robot/joint_velocity that integrates per-actuator
            # velocity into ctrl targets.
            if a == "joint_velocity":
                body = {
                    "joint_velocities": p.get("joint_velocities", []),
                    "joint_names": p.get("joint_names", []),
                }
                r = self._post("/robot/joint_velocity", body)
                if r is None:
                    return {"status": "error", "adapter": self.name,
                            "error": self._last_error}
                return r

            # ROS2 twist translation — same shape HttpAdapter uses so
            # task_executor's ros2_publish path keeps working.
            if a == "ros2_publish":
                msg_type = p.get("msg_type", "")
                data = p.get("data", {})
                if "Twist" in msg_type:
                    vx = float(data.get("linear", {}).get("x", 0))
                    wz = float(data.get("angular", {}).get("z", 0))
                    return self.send_velocity(vx, 0.0, wz)
                if "NavigateToPose" in msg_type:
                    pose = (data.get("pose") or {}).get("position") or {}
                    return self.navigate_to(
                        float(pose.get("x", 0)), float(pose.get("y", 0)),
                    )
                # Gripper / string TTS / joint trajectories aren't
                # wired to MuJoCo (yet) — report cleanly rather than
                # silently drop.
                return {"status": "ok", "adapter": self.name,
                        "note": f"ros2_publish {msg_type} not mapped for mujoco"}

        except Exception as exc:
            logger.warning("handle_action(%s) error: %s", action_type, exc)
            return {"status": "error", "adapter": self.name,
                    "error": str(exc)}

        return {"status": "unknown_action",
                "adapter": self.name, "action_type": action_type}

    # ── HTTP helpers ──────────────────────────────────────────────────

    def _get(self, path: str) -> dict | None:
        try:
            url = f"{self.robot_url}{path}"
            with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            self._last_error = f"http:{e.code}"
            if e.code != 404:
                self.connected = False
            return None
        except urllib.error.URLError as e:
            self._last_error = f"network:{e}"
            self.connected = False
            return None
        except Exception as e:
            self._last_error = f"unknown:{e}"
            self.connected = False
            return None

    def _post(self, path: str, data: dict) -> dict | None:
        try:
            url = f"{self.robot_url}{path}"
            body = json.dumps(data or {}).encode()
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            self._last_error = f"http:{e.code}"
            return None
        except urllib.error.URLError as e:
            self._last_error = f"network:{e}"
            self.connected = False
            return None
        except Exception as e:
            self._last_error = f"unknown:{e}"
            return None

    def _poll(self, path: str, timeout_s: float = 8.0,
              interval_s: float = 0.3) -> dict | None:
        """Poll a /look/result or /find/result endpoint until done=True
        or the deadline. Returns the final payload, or None on failure."""
        deadline = time.monotonic() + max(1.0, timeout_s)
        last = None
        while time.monotonic() < deadline:
            r = self._get(path)
            if r is not None:
                last = r
                if r.get("done"):
                    return r
            time.sleep(interval_s)
        return last  # may be unfinished; caller decides

    def _error_state(self) -> dict:
        return {
            "position": {"x": 0, "y": 0, "z": 0},
            "velocity": {"vx": 0, "vy": 0, "vz": 0},
            "heading_rad": 0.0, "speed_mps": 0.0,
            "battery": 95.0, "tilt_deg": 0.0,
            "mode": "ADVISORY", "timestamp_s": time.time(),
            "adapter": self.name, "error": self._last_error,
            "sensors": {},
        }
