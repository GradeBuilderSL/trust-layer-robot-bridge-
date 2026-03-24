"""MultiStepIntentParser — deterministic parser for compound operator commands.

Transforms natural language compound commands into sequences of primitive actions.
Example: "оглядись и найди куб" → ["look", "find"]

Layer: L2a (deterministic arbitration)
Constraints: NO ML/LLM/network I/O, fail-closed on error, fully deterministic.
"""
from __future__ import annotations

import re
from typing import List, Union

from ontology.action_gate import ReasonCode


class MultiStepIntentParser:
    """Deterministic parser for multi-step operator commands.
    
    Supports Russian and English languages. Uses finite-state token matching
    to decompose compound commands like "look and find" into primitive action sequences.
    
    Output: List of primitive action strings, or ReasonCode.ERROR if parsing fails.
    """
    
    # Supported primitive actions (from action_gate and robot_command)
    SUPPORTED_PRIMITIVES = {
        "look",      # осмотреться, посмотреть, оглядеться
        "find",      # найти, обнаружить, отыскать
        "scan",      # просканировать, сканировать
        "move",      # двигаться, переместиться, идти
        "grasp",     # взять, захватить, схватить
        "place",     # положить, разместить
        "rotate",    # повернуть, вращать
        "stop",      # остановиться, стоп
        "wait",      # ждать, подождать
        "report",    # сообщить, отчитаться
        "follow",    # следовать, сопровождать
        "avoid",     # избегать, обойти
        "deliver",   # доставить, принести
        "pick",      # поднять, взять
        "drop",      # бросить, опустить
    }
    
    # Conjunctions that split commands (Russian and English)
    CONJUNCTIONS = {"и", "and", "затем", "then", "после", "after", ",", "а", "но", "or"}
    
    # Verb-to-primitive mapping (Russian verbs → primitive actions)
    VERB_MAPPING = {
        # Russian verbs
        "осмотрись": "look",
        "осмотреться": "look",
        "посмотри": "look",
        "посмотреть": "look",
        "оглядись": "look",
        "оглядеться": "look",
        "найди": "find",
        "найти": "find",
        "обнаружить": "find",
        "отыскать": "find",
        "просканируй": "scan",
        "просканировать": "scan",
        "сканируй": "scan",
        "сканировать": "scan",
        "двигайся": "move",
        "двигаться": "move",
        "переместись": "move",
        "переместиться": "move",
        "иди": "move",
        "пойди": "move",
        "возьми": "grasp",
        "взять": "grasp",
        "захвати": "grasp",
        "захватить": "grasp",
        "схвати": "grasp",
        "схватить": "grasp",
        "положи": "place",
        "положить": "place",
        "размести": "place",
        "разместить": "place",
        "поверни": "rotate",
        "повернуть": "rotate",
        "вращай": "rotate",
        "вращать": "rotate",
        "остановись": "stop",
        "остановиться": "stop",
        "стоп": "stop",
        "жди": "wait",
        "ждать": "wait",
        "подожди": "wait",
        "подождать": "wait",
        "сообщи": "report",
        "сообщить": "report",
        "отчитайся": "report",
        "отчитаться": "report",
        "следуй": "follow",
        "следовать": "follow",
        "сопровождай": "follow",
        "сопровождать": "follow",
        "избегай": "avoid",
        "избегать": "avoid",
        "обойди": "avoid",
        "обойти": "avoid",
        "доставь": "deliver",
        "доставить": "deliver",
        "принеси": "deliver",
        "принести": "deliver",
        "подними": "pick",
        "поднять": "pick",
        "брось": "drop",
        "бросить": "drop",
        "опусти": "drop",
        "опустить": "drop",
        
        # English verbs
        "look": "look",
        "look around": "look",
        "find": "find",
        "scan": "scan",
        "move": "move",
        "go": "move",
        "grasp": "grasp",
        "take": "grasp",
        "place": "place",
        "put": "place",
        "rotate": "rotate",
        "turn": "rotate",
        "stop": "stop",
        "halt": "stop",
        "wait": "wait",
        "report": "report",
        "follow": "follow",
        "avoid": "avoid",
        "deliver": "deliver",
        "pick": "pick",
        "drop": "drop",
    }
    
    # Patterns to extract verbs from commands (supports verb+object patterns)
    VERB_PATTERNS = [
        r"(\w+)\s+\w+",  # verb + object
        r"(\w+)$",       # verb alone
    ]
    
    def __init__(self):
        """Initialize the parser with deterministic mappings."""
        pass
    
    def parse(self, command: str) -> Union[List[str], ReasonCode]:
        """Parse a compound command into a sequence of primitive actions.
        
        Args:
            command: Natural language command string (Russian or English)
            
        Returns:
            List of primitive action strings (e.g., ["look", "find"])
            OR ReasonCode.ERROR if parsing fails
            
        Examples:
            "оглядись и найди куб" → ["look", "find"]
            "look and find" → ["look", "find"]
            "посмотри, найди и возьми" → ["look", "find", "grasp"]
        """
        if not command or not isinstance(command, str):
            return ReasonCode.ERROR
        
        # Step 1: Normalize
        normalized = self._normalize_command(command)
        
        # Step 2: Split into segments by conjunctions
        segments = self._split_by_conjunctions(normalized)
        
        # Step 3: Map each segment to a primitive
        primitives = []
        for segment in segments:
            if not segment:
                continue
                
            primitive = self._extract_primitive(segment)
            if not primitive:
                # Unknown verb/action in segment
                return ReasonCode.ERROR
                
            primitives.append(primitive)
        
        # Step 4: Validate sequence
        if not primitives:
            return ReasonCode.ERROR
            
        # Step 5: Return parsed sequence
        return primitives
    
    def _normalize_command(self, command: str) -> str:
        """Normalize command for consistent parsing."""
        # Convert to lowercase
        normalized = command.lower()
        
        # Replace multiple spaces with single space
        normalized = re.sub(r"\s+", " ", normalized)
        
        # Remove punctuation except commas (used as conjunctions)
        normalized = re.sub(r"[!?;:]", "", normalized)
        
        # Trim whitespace
        normalized = normalized.strip()
        
        return normalized
    
    def _split_by_conjunctions(self, command: str) -> List[str]:
        """Split command into segments using conjunction words."""
        # Create regex pattern for conjunctions
        conj_pattern = r"\s+(?:" + "|".join(re.escape(c) for c in self.CONJUNCTIONS) + r")\s+"
        
        # Split by conjunctions
        segments = re.split(conj_pattern, command)
        
        # Also split by commas if they weren't captured by the pattern
        expanded_segments = []
        for segment in segments:
            # Split by commas that aren't inside word boundaries
            comma_split = re.split(r"\s*,\s*", segment)
            expanded_segments.extend([s for s in comma_split if s])
        
        return [s.strip() for s in expanded_segments if s.strip()]
    
    def _extract_primitive(self, segment: str) -> str:
        """Extract primitive action from a command segment."""
        # Direct mapping check
        if segment in self.VERB_MAPPING:
            return self.VERB_MAPPING[segment]
        
        # Try to extract verb from segment (verb + object pattern)
        for pattern in self.VERB_PATTERNS:
            match = re.search(pattern, segment)
            if match:
                verb = match.group(1)
                if verb in self.VERB_MAPPING:
                    return self.VERB_MAPPING[verb]
        
        # Check if segment starts with a known verb
        words = segment.split()
        if words and words[0] in self.VERB_MAPPING:
            return self.VERB_MAPPING[words[0]]
        
        # Check if any word in segment is a known verb
        for word in words:
            if word in self.VERB_MAPPING:
                return self.VERB_MAPPING[word]
        
        # No known verb found
        return ""
    
    def get_supported_primitives(self) -> List[str]:
        """Get list of supported primitive actions."""
        return sorted(self.SUPPORTED_PRIMITIVES)