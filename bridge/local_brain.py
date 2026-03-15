"""Local Brain — autonomous robot intelligence that runs without workstation.

Activated automatically when ConnectivityMonitor detects Wi-Fi loss.
Handles: safety checks, knowledge Q&A, exhibition FSM ticks, event buffering.

Architecture (subset of full Trust Layer):
  Safety:     SafetyPipeline (always available) + YAML rule overrides
  Q&A:        BM25-like search over /data/knowledge/ .md files
  Exhibition: Simple FSM (IDLE → GREETING → DEMO → COOLDOWN → IDLE)
  Libs:       Tries to import from libs.* (optional; falls back gracefully)

Works on both N2 (AMR) and H1 (humanoid) — thresholds loaded from
/data/active_profession/behavior_profile.yaml if present.

Usage:
    brain = LocalBrain(data_dir="/data")
    brain.load()

    # In autonomy loop:
    result = brain.process_observation({
        "robot_state": state_dict,
        "entities":    entity_list,
    })
    # → {guarded_command, safety_events, regulatory_snapshot}

    answer = brain.answer_question("Где туалет?", language="ru")
"""
from __future__ import annotations

import logging
import math
import re
import time
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional lib imports (available when PYTHONPATH includes trust-layer/libs)
# ---------------------------------------------------------------------------

try:
    from ontology.rule_engine import RuleEngine, build_context
    _HAVE_RULE_ENGINE = True
    logger.info("LocalBrain: RuleEngine available")
except ImportError:
    _HAVE_RULE_ENGINE = False
    logger.info("LocalBrain: RuleEngine not available — using SafetyPipeline only")

try:
    from world_memory.world_state import WorldState, SpatialEntity
    _HAVE_WORLD_STATE = True
except ImportError:
    _HAVE_WORLD_STATE = False

try:
    from regulatory.regulatory_state import RegulatoryState, RegulatoryConfig, RegulatoryStimulus
    _HAVE_REGULATORY = True
except ImportError:
    _HAVE_REGULATORY = False

try:
    import yaml as _yaml
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False

# SafetyPipeline is always available (in same package)
from bridge.safety_pipeline import SafetyPipeline


# ---------------------------------------------------------------------------
# Simple BM25-like knowledge search (zero external deps)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> List[str]:
    return re.findall(r"[а-яёa-z0-9]+", text.lower())


class _KnowledgeBase:
    """In-memory BM25 search over FAQ .md files."""

    K1 = 1.5
    B  = 0.75

    def __init__(self):
        self._chunks: List[dict] = []  # {text, source, tokens}
        self._avg_len: float = 0.0

    def load(self, knowledge_dir: Path) -> int:
        """Load all .md and .txt files from directory. Returns chunk count."""
        self._chunks.clear()
        count = 0
        for path in sorted(knowledge_dir.glob("*.md")) + sorted(knowledge_dir.glob("*.txt")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                for chunk in self._split(text, path.name):
                    self._chunks.append(chunk)
                    count += 1
            except Exception as exc:
                logger.warning("KnowledgeBase: failed to load %s: %s", path, exc)

        if self._chunks:
            self._avg_len = sum(len(c["tokens"]) for c in self._chunks) / len(self._chunks)
        logger.info("KnowledgeBase: loaded %d chunks from %s", count, knowledge_dir)
        return count

    def search(self, query: str, top_k: int = 3) -> List[str]:
        """BM25 search. Returns list of top matching chunk texts."""
        if not self._chunks:
            return []
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []

        # Build IDF-like weights from collection
        from collections import Counter
        doc_freq: dict = Counter()
        for chunk in self._chunks:
            for tok in set(chunk["tokens"]):
                doc_freq[tok] += 1
        N = len(self._chunks)

        scores = []
        for chunk in self._chunks:
            doc_tokens = chunk["tokens"]
            doc_len    = len(doc_tokens)
            tf_map: dict = Counter(doc_tokens)
            score = 0.0
            for tok in q_tokens:
                tf  = tf_map.get(tok, 0)
                df  = doc_freq.get(tok, 0)
                idf = math.log((N - df + 0.5) / (df + 0.5) + 1)
                norm_tf = (tf * (self.K1 + 1)) / (
                    tf + self.K1 * (1 - self.B + self.B * doc_len / max(1, self._avg_len))
                )
                score += idf * norm_tf
            scores.append(score)

        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [self._chunks[i]["text"] for i in ranked[:top_k] if scores[i] > 0]

    def _split(self, text: str, source: str) -> List[dict]:
        """Split text into ~400 char chunks with 80 char overlap."""
        size, overlap, min_len = 400, 80, 50
        chunks = []
        pos = 0
        while pos < len(text):
            end = pos + size
            chunk_text = text[pos:end].strip()
            if len(chunk_text) >= min_len:
                chunks.append({
                    "text":   chunk_text,
                    "source": source,
                    "tokens": _tokenize(chunk_text),
                })
            pos = end - overlap
        return chunks


# ---------------------------------------------------------------------------
# Exhibition FSM
# ---------------------------------------------------------------------------

class _ExhibitionFSM:
    """Simple exhibition state machine.

    States: IDLE → GREETING → DEMO → COOLDOWN → IDLE
    """

    IDLE      = "IDLE"
    GREETING  = "GREETING"
    DEMO      = "DEMO"
    COOLDOWN  = "COOLDOWN"

    _GREETING_DURATION = 8.0
    _DEMO_DURATION     = 20.0
    _COOLDOWN_DURATION = 15.0

    def __init__(self):
        self._state      = self.IDLE
        self._state_ts   = time.time()
        self._last_action: dict | None = None

    @property
    def state(self) -> str:
        return self._state

    def tick(self, entities: list) -> dict | None:
        """Tick FSM. Returns action dict if something should happen."""
        now   = time.time()
        age   = now - self._state_ts
        human = any(
            e.get("is_human") or e.get("class_name") == "person"
            for e in entities
        )

        if self._state == self.IDLE:
            if human:
                self._transition(self.GREETING)
                return {"action": "gesture", "name": "wave",
                        "speak": "Привет! Добро пожаловать!"}

        elif self._state == self.GREETING:
            if age > self._GREETING_DURATION:
                self._transition(self.DEMO)
                return {"action": "gesture", "name": "bow",
                        "speak": "Позвольте показать вам несколько возможностей!"}

        elif self._state == self.DEMO:
            if age > self._DEMO_DURATION or not human:
                self._transition(self.COOLDOWN)
                return {"action": "gesture", "name": "wave",
                        "speak": "Спасибо за внимание! Если есть вопросы — спрашивайте."}

        elif self._state == self.COOLDOWN:
            if age > self._COOLDOWN_DURATION:
                self._transition(self.IDLE)

        return None

    def _transition(self, new_state: str):
        logger.debug("ExhibitionFSM: %s → %s", self._state, new_state)
        self._state    = new_state
        self._state_ts = time.time()


# ---------------------------------------------------------------------------
# Local Brain
# ---------------------------------------------------------------------------

class LocalBrain:
    """Autonomous robot intelligence — runs without workstation connectivity.

    Loaded once at startup; can be hot-reloaded when profession changes.
    """

    def __init__(self, data_dir: str = "/data"):
        self._data_dir = Path(data_dir)
        self._loaded   = False

        # Core components
        self._pipeline          = SafetyPipeline()
        self._knowledge         = _KnowledgeBase()
        self._exhibition_fsm    = _ExhibitionFSM()
        self._world_state       = None   # optional: WorldState from libs
        self._regulatory_state  = None   # optional: RegulatoryState from libs
        self._rule_engine       = None   # optional: RuleEngine from libs

        # Runtime config (from profession YAML)
        self._profession_id     = ""
        self._robot_type        = "unknown"
        self._max_speed_mps     = 0.8

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self) -> bool:
        """Load all components from /data/. Returns True if safety rules loaded."""
        ok = True

        # 1. Load profession config → override thresholds
        self._load_profession_config()

        # 2. Optional: WorldState
        if _HAVE_WORLD_STATE:
            try:
                self._world_state = WorldState()
                logger.info("LocalBrain: WorldState loaded")
            except Exception as exc:
                logger.warning("LocalBrain: WorldState failed: %s", exc)

        # 3. Optional: RegulatoryState
        if _HAVE_REGULATORY:
            try:
                self._regulatory_state = RegulatoryState()
                logger.info("LocalBrain: RegulatoryState loaded")
            except Exception as exc:
                logger.warning("LocalBrain: RegulatoryState failed: %s", exc)

        # 4. Optional: RuleEngine (load profession safety policy YAML)
        if _HAVE_RULE_ENGINE:
            try:
                self._rule_engine = RuleEngine()
                self._rule_engine.load_builtin_rules()
                policy_path = self._data_dir / "active_profession" / "safety_policy.yaml"
                if policy_path.exists() and _HAVE_YAML:
                    self._rule_engine.load_file(str(policy_path))
                logger.info("LocalBrain: RuleEngine loaded")
            except Exception as exc:
                logger.warning("LocalBrain: RuleEngine failed: %s", exc)
                ok = False

        # 5. Knowledge base
        knowledge_dir = self._data_dir / "knowledge"
        if not knowledge_dir.exists():
            # Also try active_profession/knowledge/
            knowledge_dir = self._data_dir / "active_profession" / "knowledge"
        if knowledge_dir.exists():
            loaded = self._knowledge.load(knowledge_dir)
            if loaded == 0:
                logger.warning("LocalBrain: knowledge base empty at %s", knowledge_dir)
        else:
            logger.info("LocalBrain: no knowledge dir — Q&A will use fallback")

        self._loaded = True
        logger.info(
            "LocalBrain loaded: profession=%s robot=%s max_speed=%.2f "
            "rule_engine=%s world_state=%s knowledge_chunks=%d",
            self._profession_id, self._robot_type, self._max_speed_mps,
            bool(self._rule_engine), bool(self._world_state),
            len(self._knowledge._chunks),
        )
        return ok

    # ------------------------------------------------------------------
    # Observation processing (called at 10 Hz in autonomous mode)
    # ------------------------------------------------------------------

    def process_observation(self, obs: dict) -> dict:
        """Process one observation cycle.

        Args:
            obs: {"robot_state": {...}, "entities": [...]}

        Returns:
            {
                "guarded_command": {"vx": f, "vy": f, "wz": f, "gate": {...}},
                "safety_events":   [...],
                "regulatory_snapshot": {...},
                "exhibition_action": {...} | None,
            }
        """
        if not self._loaded:
            self.load()

        robot_state = obs.get("robot_state", {})
        entities    = obs.get("entities", [])
        cmd         = obs.get("command", {"vx": 0.0, "vy": 0.0, "wz": 0.0})

        # Safety check
        vx, vy, wz, gate = self._pipeline.check(
            cmd.get("vx", 0.0),
            cmd.get("vy", 0.0),
            cmd.get("wz", 0.0),
            robot_state,
            entities,
        )

        # Optional: update WorldState
        if self._world_state and entities:
            try:
                self._update_world_state(entities)
            except Exception:
                pass

        # Optional: update RegulatoryState
        reg_snapshot = {}
        if self._regulatory_state:
            try:
                self._update_regulatory(robot_state, entities)
                reg_snapshot = vars(self._regulatory_state.snapshot())
            except Exception:
                pass

        # Exhibition FSM tick
        exhibition_action = None
        if self._profession_id and "exhibition" in self._profession_id.lower():
            try:
                exhibition_action = self._exhibition_fsm.tick(entities)
            except Exception:
                pass

        safety_events = self._pipeline.get_reasoning(clear=True)

        return {
            "guarded_command": {
                "vx": round(vx, 4),
                "vy": round(vy, 4),
                "wz": round(wz, 4),
                "gate": {
                    "decision": gate.decision,
                    "reason":   gate.reason,
                    "rule_id":  gate.rule_id,
                },
            },
            "safety_events":       safety_events,
            "regulatory_snapshot": reg_snapshot,
            "exhibition_action":   exhibition_action,
            "exhibition_state":    self._exhibition_fsm.state,
        }

    # ------------------------------------------------------------------
    # Q&A
    # ------------------------------------------------------------------

    def answer_question(self, question: str, language: str = "ru") -> str:
        """Answer question from knowledge base (no LLM).

        Returns natural language answer string.
        """
        results = self._knowledge.search(question, top_k=3)

        if not results:
            if language == "ru":
                return (
                    "Извините, для ответа на этот вопрос мне нужно подключение к сети. "
                    "Без Wi-Fi я могу отвечать только на вопросы из базы знаний: "
                    "расписание, навигация, правила безопасности."
                )
            return (
                "Sorry, I need an internet connection to answer this question. "
                "In offline mode I can answer questions from my knowledge base: "
                "schedule, navigation, safety rules."
            )

        # Return the best matching chunk (the top BM25 result)
        best = results[0]

        # Extract the most relevant sentence (first 2–3 sentences)
        sentences = re.split(r"(?<=[.!?])\s+", best.strip())
        answer = " ".join(sentences[:3]).strip()

        # Clean markdown headers and bullets
        answer = re.sub(r"^#+\s+", "", answer, flags=re.MULTILINE)
        answer = re.sub(r"^\*\*(.+?)\*\*\s*", r"\1: ", answer)
        answer = re.sub(r"^[-*]\s+", "", answer, flags=re.MULTILINE)
        answer = answer.strip()

        return answer or results[0][:300]

    # ------------------------------------------------------------------
    # Exhibition FSM
    # ------------------------------------------------------------------

    def tick_exhibition(self, entities: list = None) -> dict | None:
        """Tick exhibition FSM. Returns action dict or None."""
        return self._exhibition_fsm.tick(entities or [])

    @property
    def exhibition_state(self) -> str:
        return self._exhibition_fsm.state

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def status_dict(self) -> dict:
        return {
            "loaded":           self._loaded,
            "profession_id":    self._profession_id,
            "robot_type":       self._robot_type,
            "max_speed_mps":    self._max_speed_mps,
            "rule_engine":      bool(self._rule_engine),
            "world_state":      bool(self._world_state),
            "regulatory_state": bool(self._regulatory_state),
            "knowledge_chunks": len(self._knowledge._chunks),
            "exhibition_state": self._exhibition_fsm.state,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_profession_config(self):
        """Load behavior_profile.yaml to override safety thresholds."""
        profile_path = self._data_dir / "active_profession" / "behavior_profile.yaml"
        manifest_path = self._data_dir / "active_profession" / "manifest.yaml"

        if not _HAVE_YAML:
            return

        import yaml

        if manifest_path.exists():
            try:
                with manifest_path.open() as f:
                    m = yaml.safe_load(f)
                self._profession_id = m.get("profession_id", "")
                robots = m.get("target_robots", [])
                self._robot_type = robots[0] if robots else "unknown"
            except Exception as exc:
                logger.warning("LocalBrain: manifest load failed: %s", exc)

        if profile_path.exists():
            try:
                with profile_path.open() as f:
                    profile = yaml.safe_load(f)

                movement = profile.get("movement", {})
                self._max_speed_mps = movement.get("max_speed_mps", self._max_speed_mps)

                # Override SafetyPipeline thresholds from profession
                if "h1" in self._robot_type.lower():
                    # H1 humanoid: tighter tilt limit, no strafe
                    self._pipeline.MAX_SPEED_MPS   = min(1.2, self._max_speed_mps)
                    self._pipeline.TILT_LIMIT_DEG  = 20.0
                else:
                    # N2 AMR: standard thresholds
                    self._pipeline.MAX_SPEED_MPS   = min(0.8, self._max_speed_mps)
                    self._pipeline.TILT_LIMIT_DEG  = 20.0

                logger.info("LocalBrain: thresholds loaded from profession: "
                            "max_speed=%.2f tilt_limit=%.1f",
                            self._pipeline.MAX_SPEED_MPS,
                            self._pipeline.TILT_LIMIT_DEG)
            except Exception as exc:
                logger.warning("LocalBrain: behavior_profile load failed: %s", exc)

    def _update_world_state(self, entities: list):
        for ent in entities:
            try:
                x, y  = ent.get("x", 0.0), ent.get("y", 0.0)
                label = ent.get("class_name", "unknown")
                conf  = ent.get("confidence", 0.8)
                eid   = ent.get("id", label)
                self._world_state.upsert(
                    entity_id=str(eid),
                    x=float(x),
                    y=float(y),
                    label=label,
                    confidence=float(conf),
                )
            except Exception:
                pass

    def _update_regulatory(self, robot_state: dict, entities: list):
        threat  = min(1.0, len([e for e in entities if e.get("is_human")]) * 0.15)
        battery = robot_state.get("battery", 100)
        confidence = max(0.3, min(1.0, (battery - 10) / 90))

        try:
            stim = RegulatoryStimulus(
                threat=threat,
                confidence=confidence,
                momentum=0.5,
            )
            self._regulatory_state.update(stim)
        except Exception:
            pass
