"""RegulatoryStateODE — 3-axis ODE model for regulatory state assessment.

Implements a deterministic ODE model tracking:
  - threat: Regulatory threat level [0, 1]
  - confidence: Assessment confidence [0, 1]  
  - momentum: Rate of change momentum [-1, 1]

Used in L2b (trust_edge) for ML/LLM trust assessment and L1 (safety_edge)
for deterministic safety threshold adjustments.

Design principles:
  - Deterministic: identical inputs → identical outputs
  - Fail-closed: errors → maximum threat level
  - Fast: ODE step < 1ms on standard CPU
  - Configurable: parameters via YAML config
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import numpy as np

# Add libs to path for imports
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

try:
    from libs.rlm.rule_lifecycle import RuleLifecycleManager
    from libs.validator_math.gate_engine import GateEngine
except ImportError:
    # Fallback for testing
    RuleLifecycleManager = None
    GateEngine = None

logger = logging.getLogger(__name__)

# Default configuration path
_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "configs" / "regulatory_state_params.yaml"


@dataclass
class RegulatoryState:
    """Current state of the regulatory ODE model."""
    threat: float  # [0, 1]
    confidence: float  # [0, 1]
    momentum: float  # [-1, 1]
    timestamp: float
    regulatory_risk: float  # Composite risk score [0, 1]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "threat": float(self.threat),
            "confidence": float(self.confidence),
            "momentum": float(self.momentum),
            "timestamp": self.timestamp,
            "regulatory_risk": float(self.regulatory_risk),
            "risk_category": self._categorize_risk()
        }
    
    def _categorize_risk(self) -> str:
        """Categorize regulatory risk based on thresholds."""
        if self.regulatory_risk > 0.7:
            return "HIGH"
        elif self.regulatory_risk > 0.4:
            return "MEDIUM"
        elif self.regulatory_risk > 0.1:
            return "LOW"
        else:
            return "NEGLIGIBLE"


class RegulatoryStateODE:
    """3-axis ODE model for regulatory state assessment."""
    
    def __init__(
        self, 
        config_path: Optional[str] = None,
        initial_state: Optional[Tuple[float, float, float]] = None
    ):
        """Initialize ODE model with configuration.
        
        Args:
            config_path: Path to YAML configuration file
            initial_state: Optional (threat, confidence, momentum) tuple
        """
        self.threat = 0.0  # Regulatory threat level [0, 1]
        self.confidence = 1.0  # Assessment confidence [0, 1]
        self.momentum = 0.0  # Change momentum [-1, 1]
        self.time = 0.0  # Model time (simulated)
        self.last_update = time.time()  # Wall-clock time
        
        # Parameters for ODE equations
        self.params = self._load_params(config_path)
        
        # External inputs tracking
        self.external_pressure = 0.0
        self.regulatory_changes = 0
        self.compliance_violations = 0
        
        # Initialize with custom state if provided
        if initial_state:
            self.threat, self.confidence, self.momentum = initial_state
        
        # Setup GateEngine for numerical validation
        self.gate_engine = GateEngine() if GateEngine else None
        
        logger.info(f"RegulatoryStateODE initialized: threat={self.threat:.3f}, "
                   f"confidence={self.confidence:.3f}, momentum={self.momentum:.3f}")
    
    def _load_params(self, config_path: Optional[str]) -> Dict[str, float]:
        """Load ODE parameters from YAML configuration.
        
        Uses RuleLifecycleManager if available, otherwise returns defaults.
        """
        default_params = {
            # ODE equation coefficients
            "alpha": 0.1,      # External pressure coefficient
            "beta": 0.05,      # Confidence damping coefficient  
            "gamma": 0.02,     # Momentum coefficient
            "delta": 0.01,     # Momentum damping
            "epsilon": 0.03,   # Confidence recovery rate
            
            # External input weights
            "pressure_weight": 0.3,
            "changes_weight": 0.2,
            "violations_weight": 0.5,
            
            # Clamping thresholds
            "max_threat": 1.0,
            "min_confidence": 0.0,
            "max_momentum": 1.0,
            "min_momentum": -1.0,
            
            # Risk calculation weights
            "threat_weight": 0.6,
            "confidence_weight": 0.3,
            "momentum_weight": 0.1,
        }
        
        # Try to load from config file
        config_to_load = config_path or str(_DEFAULT_CONFIG_PATH)
        
        if RuleLifecycleManager and os.path.exists(config_to_load):
            try:
                rlm = RuleLifecycleManager(config_to_load)
                config = rlm.get_config()
                if "regulatory_state" in config and "ode_params" in config["regulatory_state"]:
                    loaded = config["regulatory_state"]["ode_params"]
                    # Update defaults with loaded values
                    for key in default_params:
                        if key in loaded:
                            default_params[key] = loaded[key]
                    logger.info(f"Loaded regulatory ODE params from {config_to_load}")
            except Exception as e:
                logger.warning(f"Failed to load regulatory config from {config_to_load}: {e}")
        
        return default_params
    
    def _get_external_pressure(self) -> float:
        """Calculate normalized external pressure from inputs.
        
        Returns:
            Normalized pressure in [0, 1] range
        """
        # Normalize inputs
        pressure_norm = min(self.external_pressure, 1.0)
        changes_norm = min(self.regulatory_changes / 10.0, 1.0)  # Max 10 changes
        violations_norm = min(self.compliance_violations / 5.0, 1.0)  # Max 5 violations
        
        # Weighted combination
        total = (
            self.params["pressure_weight"] * pressure_norm +
            self.params["changes_weight"] * changes_norm +
            self.params["violations_weight"] * violations_norm
        )
        
        # Clamp to [0, 1]
        return max(0.0, min(1.0, total))
    
    def derivatives(self, t: float, state: np.ndarray) -> np.ndarray:
        """Compute derivatives for ODE system.
        
        Args:
            t: Current time (unused, for ODE function signature)
            state: Current state [threat, confidence, momentum]
            
        Returns:
            Derivatives [d_threat/dt, d_confidence/dt, d_momentum/dt]
        """
        threat, confidence, momentum = state
        
        # External pressure influences threat
        external_pressure = self._get_external_pressure()
        
        # Threat derivative: pressure increases threat, confidence reduces it
        d_threat = (
            self.params["alpha"] * external_pressure -
            self.params["beta"] * confidence +
            self.params["gamma"] * momentum
        )
        
        # Confidence derivative: recovers when threat is low
        d_confidence = (
            self.params["epsilon"] * (1.0 - threat) -
            self.params["beta"] * threat
        )
        
        # Momentum derivative: changes in threat drive momentum
        d_momentum = (
            self.params["gamma"] * d_threat -
            self.params["delta"] * momentum
        )
        
        return np.array([d_threat, d_confidence, d_momentum])
    
    def step(self, dt: float = 1.0, external_inputs: Optional[Dict[str, Any]] = None) -> RegulatoryState:
        """Advance ODE model by one time step.
        
        Uses 4th-order Runge-Kutta method for numerical integration.
        
        Args:
            dt: Time step in seconds
            external_inputs: Dictionary of external inputs:
                - external_pressure: float [0, 1]
                - regulatory_changes: int
                - compliance_violations: int
                
        Returns:
            Updated regulatory state
            
        Raises:
            ValueError: If ODE integration fails (fail-closed: sets threat to max)
        """
        try:
            # Update external inputs if provided
            if external_inputs:
                self.external_pressure = external_inputs.get("external_pressure", 0.0)
                self.regulatory_changes = external_inputs.get("regulatory_changes", 0)
                self.compliance_violations = external_inputs.get("compliance_violations", 0)
            
            # Current state vector
            state = np.array([self.threat, self.confidence, self.momentum])
            
            # 4th-order Runge-Kutta integration
            k1 = self.derivatives(self.time, state)
            k2 = self.derivatives(self.time + dt/2, state + dt*k1/2)
            k3 = self.derivatives(self.time + dt/2, state + dt*k2/2)
            k4 = self.derivatives(self.time + dt, state + dt*k3)
            
            new_state = state + dt/6 * (k1 + 2*k2 + 2*k3 + k4)
            
            # Clamp to valid ranges
            self.threat = max(0.0, min(self.params["max_threat"], new_state[0]))
            self.confidence = max(self.params["min_confidence"], min(1.0, new_state[1]))
            self.momentum = max(self.params["min_momentum"], 
                               min(self.params["max_momentum"], new_state[2]))
            
            # Update time
            self.time += dt
            self.last_update = time.time()
            
            # Validate with GateEngine if available
            if self.gate_engine:
                if not self.gate_engine.validate_number(self.threat, 0.0, 1.0):
                    logger.error(f"Threat value out of bounds: {self.threat}")
                    self.threat = 1.0  # Fail-closed
                if not self.gate_engine.validate_number(self.confidence, 0.0, 1.0):
                    logger.error(f"Confidence value out of bounds: {self.confidence}")
                    self.confidence = 0.0  # Fail-closed
            
            return self.get_state()
            
        except Exception as e:
            logger.error(f"ODE integration failed: {e}")
            # Fail-closed: maximum threat, minimum confidence
            self.threat = self.params["max_threat"]
            self.confidence = self.params["min_confidence"]
            self.momentum = 0.0
            raise ValueError(f"RegulatoryStateODE step failed: {e}")
    
    def get_state(self) -> RegulatoryState:
        """Get current regulatory state with computed risk score.
        
        Returns:
            RegulatoryState object with current state and risk assessment
        """
        # Calculate composite regulatory risk
        regulatory_risk = (
            self.params["threat_weight"] * self.threat +
            self.params["confidence_weight"] * (1.0 - self.confidence) +
            self.params["momentum_weight"] * abs(self.momentum)
        )
        regulatory_risk = max(0.0, min(1.0, regulatory_risk))
        
        return RegulatoryState(
            threat=self.threat,
            confidence=self.confidence,
            momentum=self.momentum,
            timestamp=self.last_update,
            regulatory_risk=regulatory_risk
        )
    
    def reset(self, state: Optional[Tuple[float, float, float]] = None) -> None:
        """Reset ODE model to initial state.
        
        Args:
            state: Optional (threat, confidence, momentum) tuple to reset to
        """
        if state:
            self.threat, self.confidence, self.momentum = state
        else:
            self.threat = 0.0
            self.confidence = 1.0
            self.momentum = 0.0
        self.time = 0.0
        self.last_update = time.time()
        
        # Reset external inputs
        self.external_pressure = 0.0
        self.regulatory_changes = 0
        self.compliance_violations = 0
        
        logger.info("RegulatoryStateODE reset")
    
    def update_external_inputs(self, inputs: Dict[str, Any]) -> None:
        """Update external inputs without advancing ODE.
        
        Args:
            inputs: Dictionary with external_pressure, regulatory_changes, 
                   compliance_violations
        """
        if "external_pressure" in inputs:
            self.external_pressure = max(0.0, min(1.0, inputs["external_pressure"]))
        if "regulatory_changes" in inputs:
            self.regulatory_changes = max(0, inputs["regulatory_changes"])
        if "compliance_violations" in inputs:
            self.compliance_violations = max(0, inputs["compliance_violations"])