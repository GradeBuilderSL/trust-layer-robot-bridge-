"""
Observation store for managing collections of observations with temporal decay.
"""
from typing import Dict, List, Optional, Tuple
import threading
import time
import math


class ObservationStore:
    """Thread-safe storage for observations with automatic cleanup and search capabilities."""
    
    def __init__(self, cleanup_interval: float = 60.0):
        """
        Initialize the observation store.
        
        Args:
            cleanup_interval: How often to clean up expired observations (in seconds)
        """
        self._observations: Dict[str, 'Observation'] = {}
        self._lock = threading.Lock()
        self._cleanup_interval = cleanup_interval
        self._last_cleanup = time.time()
    
    def add(self, observation: 'Observation') -> str:
        """Add an observation to the store and return its ID."""
        with self._lock:
            self._observations[observation.id] = observation
            return observation.id
    
    def get(self, observation_id: str) -> Optional['Observation']:
        """Get an observation by ID, cleaning up expired ones first."""
        with self._lock:
            self._cleanup_expired()
            return self._observations.get(observation_id)
    
    def find_by_direction(
        self, 
        direction: Dict[str, float], 
        threshold: float = 0.8,
        include_expired: bool = False
    ) -> List['Observation']:
        """Find observations similar to the given direction vector."""
        with self._lock:
            self._cleanup_expired()
            
            results = []
            current_time = time.time()
            
            for obs in self._observations.values():
                if not include_expired and obs.is_expired(current_time):
                    continue
                
                similarity = self._cosine_similarity(obs.direction, direction)
                if similarity >= threshold:
                    results.append(obs)
            
            return results
    
    def find_by_time_range(
        self, 
        start: float, 
        end: float,
        include_expired: bool = False
    ) -> List['Observation']:
        """Find observations within the given time range."""
        with self._lock:
            self._cleanup_expired()
            
            results = []
            current_time = time.time()
            
            for obs in self._observations.values():
                if not include_expired and obs.is_expired(current_time):
                    continue
                
                if start <= obs.timestamp <= end:
                    results.append(obs)
            
            return results
    
    def remove_expired(self) -> int:
        """Remove all expired observations and return count removed."""
        with self._lock:
            current_time = time.time()
            initial_count = len(self._observations)
            
            expired_ids = [
                oid for oid, obs in self._observations.items()
                if obs.is_expired(current_time)
            ]
            
            for oid in expired_ids:
                del self._observations[oid]
            
            return initial_count - len(self._observations)
    
    def get_stats(self) -> Dict:
        """Get statistics about the observation store."""
        with self._lock:
            current_time = time.time()
            self._cleanup_expired()
            
            total_count = len(self._observations)
            expired_count = sum(1 for obs in self._observations.values() if obs.is_expired(current_time))
            active_count = total_count - expired_count
            
            if total_count > 0:
                avg_confidence = sum(obs.confidence for obs in self._observations.values()) / total_count
                avg_decay_rate = sum(obs.decay_rate for obs in self._observations.values()) / total_count
            else:
                avg_confidence = 0.0
                avg_decay_rate = 0.0
            
            return {
                'total_observations': total_count,
                'active_observations': active_count,
                'expired_observations': expired_count,
                'average_confidence': avg_confidence,
                'average_decay_rate': avg_decay_rate,
                'last_cleanup_time': self._last_cleanup
            }
    
    def remove(self, observation_id: str) -> bool:
        """Remove a specific observation by ID."""
        with self._lock:
            if observation_id in self._observations:
                del self._observations[observation_id]
                return True
            return False
    
    def clear_all(self) -> int:
        """Clear all observations and return count cleared."""
        with self._lock:
            count = len(self._observations)
            self._observations.clear()
            return count
    
    def _cleanup_expired(self) -> None:
        """Internal method to cleanup expired observations if enough time has passed."""
        current_time = time.time()
        if current_time - self._last_cleanup >= self._cleanup_interval:
            self.remove_expired()
            self._last_cleanup = current_time
    
    def _cosine_similarity(self, vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
        """Calculate cosine similarity between two direction vectors."""
        # Get common keys
        keys = set(vec1.keys()) & set(vec2.keys())
        if not keys:
            return 0.0  # No common dimensions
        
        # Calculate dot product
        dot_product = sum(vec1[k] * vec2[k] for k in keys)
        
        # Calculate magnitudes
        mag1 = math.sqrt(sum(vec1[k] ** 2 for k in keys))
        mag2 = math.sqrt(sum(vec2[k] ** 2 for k in keys))
        
        if mag1 == 0.0 or mag2 == 0.0:
            return 0.0  # Avoid division by zero
        
        return dot_product / (mag1 * mag2)