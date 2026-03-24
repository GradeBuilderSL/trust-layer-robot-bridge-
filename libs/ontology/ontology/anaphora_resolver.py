"""Anaphora and deixis resolver for natural language commands."""

from __future__ import annotations

import re
from typing import List, Dict, Tuple, Optional, Any
from .context_manager import DialogueContextManager, DialogueTurn
from .world_model import Object3D


class AnaphoraResolver:
    """Resolves anaphoric expressions and deictic references in text."""
    
    def __init__(self, context_manager: DialogueContextManager) -> None:
        self.context = context_manager
        self.deictic_patterns = {
            "сюда": ["here", "to_this_place", "сюда", "сюды"],
            "туда": ["there", "to_that_place", "туда", "туды"],
            "этот": ["this", "this_one", "этот", "эта", "это", "эти"],
            "тот": ["that", "that_one", "тот", "та", "то", "те"],
        }
        
        # Compile regex patterns for efficiency
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Compile regex patterns for reference detection."""
        self.deictic_regex = {}
        for key, patterns in self.deictic_patterns.items():
            pattern = r'\b(?:' + '|'.join(re.escape(p) for p in patterns) + r')\b'
            self.deictic_regex[key] = re.compile(pattern, re.IGNORECASE)

    def resolve_references(self, text: str, current_objects: List[Object3D]) -> Tuple[str, Dict[str, Object3D]]:
        """
        Resolve anaphoric and deictic references in text.
        
        Returns:
            Tuple of (resolved_text, object_mappings)
        """
        # 1. Find anaphoric expressions
        references = self._find_references(text)
        
        # 2. For each reference, find candidates
        resolved = []
        object_mappings: Dict[str, Object3D] = {}
        
        for ref in references:
            target = None
            if ref["type"] == "deictic_place":
                target = self._resolve_deictic_place(ref, current_objects)
            elif ref["type"] == "anaphoric_object":
                target = self._resolve_anaphoric_object(ref, current_objects)
            elif ref["type"] == "demonstrative":
                target = self._resolve_demonstrative(ref, current_objects)
            
            if target:
                resolved.append((ref, target))
                object_mappings[ref["text"]] = target
        
        # 3. Replace in text
        resolved_text = self._replace_in_text(text, resolved)
        
        return resolved_text, object_mappings

    def _find_references(self, text: str) -> List[Dict[str, Any]]:
        """Find anaphoric and deictic references in text."""
        references = []
        
        # Find deictic expressions
        for ref_type, pattern in self.deictic_regex.items():
            for match in pattern.finditer(text):
                references.append({
                    "type": "deictic_place" if ref_type in ["сюда", "туда"] else "demonstrative",
                    "text": match.group(),
                    "start": match.start(),
                    "end": match.end(),
                    "ref_type": ref_type
                })
        
        # Find anaphoric object references (e.g., "тот куб", "эта коробка")
        anaphoric_pattern = re.compile(
            r'\b(этот|тот|эта|та|это|то|эти|те)\s+(\w+)', 
            re.IGNORECASE
        )
        for match in anaphoric_pattern.finditer(text):
            references.append({
                "type": "anaphoric_object",
                "text": match.group(),
                "start": match.start(),
                "end": match.end(),
                "demonstrative": match.group(1),
                "object_type": match.group(2)
            })
        
        # Sort by position to maintain order
        references.sort(key=lambda x: x["start"])
        return references

    def _resolve_deictic_place(self, ref: Dict[str, Any], current_objects: List[Object3D]) -> Optional[Object3D]:
        """Resolve deictic place references like 'сюда' (here), 'туда' (there)."""
        # For "here", use the most recently mentioned object or the closest object
        if ref["ref_type"] == "сюда":
            recent_objects = self.context.get_recent_objects(seconds=5.0)
            if recent_objects:
                return recent_objects[0]  # Most recent
            elif current_objects:
                # Return the closest object
                closest = min(current_objects, key=lambda obj: getattr(obj, 'distance_m', float('inf')))
                return closest
        # For "there", use a previously mentioned object that's not the most recent
        elif ref["ref_type"] == "туда":
            recent_objects = self.context.get_recent_objects(seconds=10.0)
            if len(recent_objects) > 1:
                return recent_objects[1]  # Second most recent
            elif current_objects and len(current_objects) > 1:
                # Return the second closest object
                sorted_objects = sorted(current_objects, key=lambda obj: getattr(obj, 'distance_m', float('inf')))
                return sorted_objects[1] if len(sorted_objects) > 1 else sorted_objects[0]
        
        return None

    def _resolve_anaphoric_object(self, ref: Dict[str, Any], current_objects: List[Object3D]) -> Optional[Object3D]:
        """Resolve anaphoric object references like 'тот куб' (that cube)."""
        object_type = ref["object_type"].lower()
        demonstrative = ref["demonstrative"].lower()
        
        # Get recent objects from context
        recent_objects = self.context.get_recent_objects(seconds=10.0)
        
        # Filter by object type
        type_matches = [obj for obj in recent_objects if object_type in (getattr(obj, 'class_name', '') or '').lower()]
        
        if not type_matches:
            # Try to match with current objects
            type_matches = [obj for obj in current_objects if object_type in (getattr(obj, 'class_name', '') or '').lower()]
        
        if type_matches:
            # "этот" refers to the most recent, "тот" refers to older ones
            if demonstrative in ["этот", "эта", "это", "эти"]:
                return type_matches[0]  # Most recent
            else:  # "тот", "та", "то", "те"
                return type_matches[-1] if len(type_matches) > 1 else type_matches[0]  # Less recent
        
        return None

    def _resolve_demonstrative(self, ref: Dict[str, Any], current_objects: List[Object3D]) -> Optional[Object3D]:
        """Resolve standalone demonstratives like 'это' (this/it)."""
        # "это" typically refers to the most salient/recent object
        recent_objects = self.context.get_recent_objects(seconds=5.0)
        if recent_objects:
            return recent_objects[0]
        elif current_objects:
            return current_objects[0]
        return None

    def _replace_in_text(self, text: str, resolved: List[Tuple[Dict[str, Any], Object3D]]) -> str:
        """Replace references in text with resolved object identifiers."""
        if not resolved:
            return text
            
        # Process replacements from end to start to maintain correct indices
        result = text
        for ref, obj in reversed(resolved):
            start, end = ref["start"], ref["end"]
            obj_id = getattr(obj, 'entity_id', 'unknown')
            replacement = f"{ref['text']}[{obj_id}]"
            result = result[:start] + replacement + result[end:]
        
        return result