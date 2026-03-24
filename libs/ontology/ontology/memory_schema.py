"""MemorySchema — schema for validating memory facts against ontology.

Defines entity types and relation types for the Trust Layer long-term memory.
"""

from enum import Enum
from typing import List, Optional, Set, Tuple


class EntityType(Enum):
    """Types of entities that can appear in memory facts."""
    OBJECT = "object"
    ZONE = "zone"
    OPERATOR = "operator"
    ROBOT = "robot"
    TASK = "task"
    LOCATION = "location"
    PREFERENCE = "preference"
    PROPERTY = "property"
    CATEGORY = "category"
    SKILL = "skill"


class RelationType(Enum):
    """Types of relations between entities."""
    LOCATED_IN = "locatedIn"
    PREFERS = "prefers"
    USUALLY_IN = "usuallyIn"
    CAPABLE_OF = "capableOf"
    HAS_PROPERTY = "hasProperty"
    HAS_CATEGORY = "hasCategory"
    EXECUTES = "executes"
    KNOWS = "knows"
    OBSERVED_AT = "observedAt"
    REQUIRES = "requires"
    PART_OF = "partOf"
    CONNECTED_TO = "connectedTo"
    NEAR = "near"
    FACING = "facing"
    OCCUPIES = "occupies"


class MemorySchema:
    """Validator for triples against ontology schema."""
    
    # Valid subject-predicate-object patterns
    _VALID_PATTERNS = {
        (EntityType.OBJECT, RelationType.LOCATED_IN, EntityType.ZONE),
        (EntityType.OBJECT, RelationType.LOCATED_IN, EntityType.LOCATION),
        (EntityType.OBJECT, RelationType.USUALLY_IN, EntityType.ZONE),
        (EntityType.OBJECT, RelationType.HAS_PROPERTY, EntityType.PROPERTY),
        (EntityType.OBJECT, RelationType.HAS_CATEGORY, EntityType.CATEGORY),
        (EntityType.OBJECT, RelationType.PART_OF, EntityType.OBJECT),
        (EntityType.OBJECT, RelationType.CONNECTED_TO, EntityType.OBJECT),
        (EntityType.OBJECT, RelationType.NEAR, EntityType.OBJECT),
        (EntityType.OBJECT, RelationType.FACING, EntityType.OBJECT),
        (EntityType.OBJECT, RelationType.OCCUPIES, EntityType.LOCATION),
        
        (EntityType.OPERATOR, RelationType.PREFERS, EntityType.PREFERENCE),
        (EntityType.OPERATOR, RelationType.KNOWS, EntityType.SKILL),
        (EntityType.OPERATOR, RelationType.EXECUTES, EntityType.TASK),
        (EntityType.OPERATOR, RelationType.REQUIRES, EntityType.SKILL),
        
        (EntityType.ROBOT, RelationType.LOCATED_IN, EntityType.ZONE),
        (EntityType.ROBOT, RelationType.CAPABLE_OF, EntityType.SKILL),
        (EntityType.ROBOT, RelationType.EXECUTES, EntityType.TASK),
        (EntityType.ROBOT, RelationType.KNOWS, EntityType.SKILL),
        (EntityType.ROBOT, RelationType.REQUIRES, EntityType.SKILL),
        
        (EntityType.TASK, RelationType.REQUIRES, EntityType.SKILL),
        (EntityType.TASK, RelationType.LOCATED_IN, EntityType.ZONE),
        (EntityType.TASK, RelationType.OBSERVED_AT, EntityType.LOCATION),
        
        (EntityType.ZONE, RelationType.CONNECTED_TO, EntityType.ZONE),
        (EntityType.ZONE, RelationType.PART_OF, EntityType.LOCATION),
        
        (EntityType.LOCATION, RelationType.PART_OF, EntityType.ZONE),
    }
    
    # Entity patterns for inference
    _ENTITY_PATTERNS = {
        r"^cube_\d+$": EntityType.OBJECT,
        r"^zone_[A-Z]$": EntityType.ZONE,
        r"^operator_\w+$": EntityType.OPERATOR,
        r"^robot_\w+$": EntityType.ROBOT,
        r"^task_\d+$": EntityType.TASK,
        r"^loc_\d+$": EntityType.LOCATION,
        r"^pref_\w+$": EntityType.PREFERENCE,
        r"^prop_\w+$": EntityType.PROPERTY,
        r"^cat_\w+$": EntityType.CATEGORY,
        r"^skill_\w+$": EntityType.SKILL,
        r"^brief_responses$": EntityType.PREFERENCE,
        r"^detailed_reports$": EntityType.PREFERENCE,
        r"^LoadingBay$": EntityType.ZONE,
        r"^Warehouse$": EntityType.LOCATION,
    }
    
    @classmethod
    def infer_entity_type(cls, entity: str) -> Optional[EntityType]:
        """Infer entity type from its name pattern."""
        import re
        for pattern, entity_type in cls._ENTITY_PATTERNS.items():
            if re.match(pattern, entity):
                return entity_type
        return None
    
    @classmethod
    def infer_relation_type(cls, predicate: str) -> Optional[RelationType]:
        """Infer relation type from predicate string."""
        try:
            return RelationType(predicate)
        except ValueError:
            # Check for similar predicates
            for rel in RelationType:
                if rel.value.lower() == predicate.lower():
                    return rel
            return None
    
    @classmethod
    def validate_triple(cls, subject: str, predicate: str, obj: str) -> Tuple[bool, str]:
        """
        Validate triple against schema.
        Returns (is_valid, error_message).
        """
        # Infer types
        subj_type = cls.infer_entity_type(subject)
        pred_type = cls.infer_relation_type(predicate)
        obj_type = cls.infer_entity_type(obj)
        
        # If we can't infer all types, be permissive but warn
        if not all([subj_type, pred_type, obj_type]):
            return True, "types not fully inferred"
        
        # Check if pattern is valid
        pattern = (subj_type, pred_type, obj_type)
        if pattern in cls._VALID_PATTERNS:
            return True, ""
        
        # Generate error message
        error_msg = f"Invalid triple pattern: {subj_type.value if subj_type else '?'} " \
                   f"{pred_type.value if pred_type else '?'} " \
                   f"{obj_type.value if obj_type else '?'}"
        return False, error_msg
    
    @classmethod
    def suggest_corrections(cls, subject: str, predicate: str, obj: str) -> List[str]:
        """Suggest corrections for invalid triple."""
        suggestions = []
        
        subj_type = cls.infer_entity_type(subject)
        pred_type = cls.infer_relation_type(predicate)
        obj_type = cls.infer_entity_type(obj)
        
        if not pred_type:
            # Suggest similar predicates
            predicate_lower = predicate.lower()
            for rel in RelationType:
                if predicate_lower in rel.value.lower() or rel.value.lower() in predicate_lower:
                    suggestions.append(f"Try predicate: {rel.value}")
        
        # Find valid patterns for the subject type
        if subj_type:
            valid_for_subj = [(p, o) for s, p, o in cls._VALID_PATTERNS if s == subj_type]
            if valid_for_subj:
                suggestions.append(f"Valid patterns for {subj_type.value}:")
                for p, o in valid_for_subj[:3]:  # Limit to 3
                    suggestions.append(f"  - {p.value} {o.value}")
        
        return suggestions