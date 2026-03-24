"""Schemas for ontology layer."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

from core_types.schemas import BaseSchema


class TrustRequest(BaseSchema):
    """Request for trust evaluation."""
    prompt: str
    context: Optional[Dict[str, Any]] = None
    max_tokens: Optional[int] = Field(default=1000, ge=1, le=4000)
    temperature: Optional[float] = Field(default=0.7, ge=0.0, le=1.0)
    stream: Optional[bool] = Field(default=False)


class TrustResponse(BaseSchema):
    """Response from trust evaluation."""
    response: str
    trust_score: float = Field(ge=0.0, le=1.0)
    reasoning: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    finish_reason: Optional[str] = None


class StreamingTrustRequest(TrustRequest):
    """Request for streaming trust evaluation."""
    stream: bool = Field(default=True)


class StreamingChunk(BaseSchema):
    """A single chunk in streaming response."""
    chunk_id: int
    content: str
    is_final: bool = False
    trust_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None


class StreamingComplete(BaseSchema):
    """Final message in streaming response."""
    final_response: str
    trust_score: float = Field(ge=0.0, le=1.0)
    total_tokens: int
    finish_reason: str
    generated_at: datetime


class LLMConfig(BaseSchema):
    """Configuration for LLM requests."""
    model: str = "gpt-3.5-turbo"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    timeout: int = 30
    max_retries: int = 3