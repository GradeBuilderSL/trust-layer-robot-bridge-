"""Stability classes and confidence decay rates for VLM observations.

Defines decay rates for different stability classes to handle temporal
aspects of object observations similar to how they're handled in world memory.

Classes:
  - structural: Permanent objects like walls, fixtures (very slow decay)
  - semi_static: Furniture, equipment (slow decay)
  - dynamic: Moving objects, tools (medium decay) 
  - ephemeral: Humans, carts, temporary items (fast decay)
  - unknown: Default for unclassified objects (medium decay)
"""

# Confidence decay rates per stability class (exponential decay: exp(-rate * time))
# Units: decay per second
CONFIDENCE_DECAY_RATES = {
    "structural": 0.01,      # 1% decay per second (permanent objects)
    "semi_static": 0.025,    # 2.5% decay per second (furniture, equipment)  
    "dynamic": 0.05,         # 5% decay per second (moving objects, tools)
    "ephemeral": 0.1,        # 10% decay per second (humans, carts)
    "unknown": 0.05,         # 5% decay per second (default)
}

# Standard stability class names
STABILITY_CLASSES = {
    "STRUCTURAL": "structural",
    "SEMI_STATIC": "semi_static", 
    "DYNAMIC": "dynamic",
    "EPHEMERAL": "ephemeral",
    "UNKNOWN": "unknown",
}

# Default stability class for new observations
DEFAULT_STABILITY_CLASS = "unknown"