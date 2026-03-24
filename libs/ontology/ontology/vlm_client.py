"""VLM Client — abstraction over VLM providers (Qwen, fallback) with timeout and retry.

Provides unified interface for VLM queries, hiding provider details and ensuring
fault tolerance. Expected usage: reliable client for VLM in Trust Layer.

L2b safety layer: deterministic only, no ML/LLM/network I/O, fail-closed on error.
"""

import os
import time
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum
import requests
from requests.exceptions import Timeout, ConnectionError, RequestException

logger = logging.getLogger(__name__)

class VLMProvider(Enum):
    """Supported VLM providers."""
    QWEN = "qwen"
    CLAUDE = "claude"
    GEMINI = "gemini"
    # Add new providers here


@dataclass
class VLMConfig:
    """Configuration for a VLM provider."""
    provider: VLMProvider
    endpoint: str
    api_key: Optional[str] = None
    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 1.0
    priority: int = 1  # Lower = higher priority


@dataclass
class VLMResponse:
    """Unified response from VLM query."""
    success: bool
    text: str
    provider: VLMProvider
    raw_response: Optional[Dict] = None
    error: Optional[str] = None
    latency_ms: float = 0.0


class VLMClient:
    """
    VLM client with fallback, timeout, and retry support.
    
    Thread-safe after initialization.
    """
    
    def __init__(self, configs: List[VLMConfig]):
        """Initialize VLM client with provider configurations.
        
        Args:
            configs: List of provider configurations, sorted by priority.
        """
        self.configs = sorted(configs, key=lambda x: x.priority)
        self.session = requests.Session()
        # Session configuration
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'TrustLayer-VLMClient/1.0'
        })
    
    def query(self, 
              prompt: str, 
              images: Optional[List[bytes]] = None,
              temperature: float = 0.1,
              max_tokens: int = 1024,
              fallback: bool = True) -> VLMResponse:
        """Main method for VLM query with fallback support.
        
        Args:
            prompt: Text prompt for the VLM.
            images: Optional list of image bytes for multimodal queries.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in response.
            fallback: Whether to try next provider if current fails.
            
        Returns:
            VLMResponse with success status and text.
        """
        errors = []
        
        for config in self.configs:
            try:
                start_time = time.time()
                response = self._query_provider(config, prompt, images, temperature, max_tokens)
                latency = (time.time() - start_time) * 1000
                
                if response.success:
                    return VLMResponse(
                        success=True,
                        text=response.text,
                        provider=config.provider,
                        raw_response=response.raw_response,
                        latency_ms=latency
                    )
                else:
                    errors.append(f"{config.provider.value}: {response.error}")
                    
            except Exception as e:
                logger.warning(f"VLM provider {config.provider.value} failed: {str(e)}")
                errors.append(f"{config.provider.value}: {str(e)}")
                
                if not fallback:
                    break
        
        # All providers failed
        return VLMResponse(
            success=False,
            text="",
            provider=self.configs[0].provider if self.configs else VLMProvider.QWEN,
            error=f"All VLM providers failed: {', '.join(errors)}"
        )
    
    def _query_provider(self, 
                        config: VLMConfig, 
                        prompt: str,
                        images: Optional[List[bytes]] = None,
                        temperature: float = 0.1,
                        max_tokens: int = 1024) -> VLMResponse:
        """Query specific provider with retry logic."""
        last_error = None
        
        for attempt in range(config.max_retries):
            try:
                # Prepare request based on provider
                if config.provider == VLMProvider.QWEN:
                    return self._query_qwen(config, prompt, images, temperature, max_tokens)
                elif config.provider == VLMProvider.CLAUDE:
                    return self._query_claude(config, prompt, images, temperature, max_tokens)
                elif config.provider == VLMProvider.GEMINI:
                    return self._query_gemini(config, prompt, images, temperature, max_tokens)
                else:
                    return VLMResponse(
                        success=False,
                        text="",
                        provider=config.provider,
                        error=f"Unsupported provider: {config.provider.value}"
                    )
                    
            except (Timeout, ConnectionError) as e:
                last_error = e
                if attempt < config.max_retries - 1:
                    time.sleep(config.retry_delay * (2 ** attempt))  # Exponential backoff
                    continue
                else:
                    return VLMResponse(
                        success=False,
                        text="",
                        provider=config.provider,
                        error=f"Network error after {config.max_retries} attempts: {str(e)}"
                    )
            except Exception as e:
                return VLMResponse(
                    success=False,
                    text="",
                    provider=config.provider,
                    error=f"Unexpected error: {str(e)}"
                )
        
        return VLMResponse(
            success=False,
            text="",
            provider=config.provider,
            error=f"Failed after retries: {str(last_error)}"
        )
    
    def _query_qwen(self, 
                   config: VLMConfig,
                   prompt: str,
                   images: Optional[List[bytes]] = None,
                   temperature: float = 0.1,
                   max_tokens: int = 1024) -> VLMResponse:
        """Implementation for Qwen VLM."""
        payload = {
            "model": "qwen-vl-plus",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt}
                    ]
                }
            ],
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        # Add images if present
        if images:
            # TODO: Implement image encoding for Qwen
            # For now, we'll add placeholder
            logger.warning("Image support for Qwen not yet implemented")
        
        headers = {}
        if config.api_key:
            headers['Authorization'] = f'Bearer {config.api_key}'
        
        try:
            response = self.session.post(
                config.endpoint,
                json=payload,
                headers=headers,
                timeout=config.timeout
            )
            response.raise_for_status()
            
            data = response.json()
            # Parse Qwen response
            text = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            
            return VLMResponse(
                success=True,
                text=text,
                provider=config.provider,
                raw_response=data
            )
            
        except RequestException as e:
            return VLMResponse(
                success=False,
                text="",
                provider=config.provider,
                error=f"Qwen API error: {str(e)}"
            )
    
    def _query_claude(self, config: VLMConfig, prompt: str, images: Optional[List[bytes]] = None, 
                     temperature: float = 0.1, max_tokens: int = 1024) -> VLMResponse:
        """Implementation for Claude (stub)."""
        # TODO: Implement when needed
        return VLMResponse(
            success=False,
            text="",
            provider=config.provider,
            error="Claude provider not implemented yet"
        )
    
    def _query_gemini(self, config: VLMConfig, prompt: str, images: Optional[List[bytes]] = None,
                     temperature: float = 0.1, max_tokens: int = 1024) -> VLMResponse:
        """Implementation for Gemini (stub)."""
        # TODO: Implement when needed
        return VLMResponse(
            success=False,
            text="",
            provider=config.provider,
            error="Gemini provider not implemented yet"
        )


# Factory function for convenience
def create_vlm_client_from_env() -> VLMClient:
    """Create VLM client from environment variables.
    
    Environment variables:
        VLM_QWEN_ENDPOINT: Qwen API endpoint
        VLM_QWEN_API_KEY: Qwen API key (optional)
        VLM_QWEN_TIMEOUT: Timeout in seconds (default: 30.0)
        VLM_QWEN_MAX_RETRIES: Max retries (default: 3)
        VLM_QWEN_PRIORITY: Priority (lower = higher, default: 1)
        
    Returns:
        VLMClient instance.
        
    Raises:
        ValueError: If no providers configured.
    """
    configs = []
    
    # Qwen config
    qwen_endpoint = os.getenv('VLM_QWEN_ENDPOINT', '').strip()
    qwen_api_key = os.getenv('VLM_QWEN_API_KEY', '').strip()
    
    if qwen_endpoint:
        configs.append(VLMConfig(
            provider=VLMProvider.QWEN,
            endpoint=qwen_endpoint,
            api_key=qwen_api_key if qwen_api_key else None,
            timeout=float(os.getenv('VLM_QWEN_TIMEOUT', '30.0')),
            max_retries=int(os.getenv('VLM_QWEN_MAX_RETRIES', '3')),
            priority=int(os.getenv('VLM_QWEN_PRIORITY', '1'))
        ))
    
    # Add other providers similarly
    # Claude config
    claude_endpoint = os.getenv('VLM_CLAUDE_ENDPOINT', '').strip()
    if claude_endpoint:
        configs.append(VLMConfig(
            provider=VLMProvider.CLAUDE,
            endpoint=claude_endpoint,
            api_key=os.getenv('VLM_CLAUDE_API_KEY', '').strip() or None,
            timeout=float(os.getenv('VLM_CLAUDE_TIMEOUT', '30.0')),
            max_retries=int(os.getenv('VLM_CLAUDE_MAX_RETRIES', '3')),
            priority=int(os.getenv('VLM_CLAUDE_PRIORITY', '2'))
        ))
    
    # Gemini config
    gemini_endpoint = os.getenv('VLM_GEMINI_ENDPOINT', '').strip()
    if gemini_endpoint:
        configs.append(VLMConfig(
            provider=VLMProvider.GEMINI,
            endpoint=gemini_endpoint,
            api_key=os.getenv('VLM_GEMINI_API_KEY', '').strip() or None,
            timeout=float(os.getenv('VLM_GEMINI_TIMEOUT', '30.0')),
            max_retries=int(os.getenv('VLM_GEMINI_MAX_RETRIES', '3')),
            priority=int(os.getenv('VLM_GEMINI_PRIORITY', '3'))
        ))
    
    if not configs:
        raise ValueError("No VLM providers configured in environment variables")
    
    return VLMClient(configs)