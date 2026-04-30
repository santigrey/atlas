"""Atlas inference Pydantic models for Ollama API request/response."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str  # 'system' | 'user' | 'assistant'
    content: str


class _OllamaTelemetryFields(BaseModel):
    """Common telemetry fields returned by both /api/generate and /api/chat."""

    model: str
    created_at: str | None = None
    prompt_eval_count: int | None = None
    eval_count: int | None = None
    total_duration: int | None = None  # nanoseconds, per Ollama convention
    load_duration: int | None = None
    prompt_eval_duration: int | None = None
    eval_duration: int | None = None
    done: bool = False
    done_reason: str | None = None


class GenerateResponse(_OllamaTelemetryFields):
    response: str


class GenerateChunk(_OllamaTelemetryFields):
    """Single chunk from streaming /api/generate. Final chunk has done=True."""

    response: str = ""


class ChatResponse(_OllamaTelemetryFields):
    message: ChatMessage


class ChatChunk(_OllamaTelemetryFields):
    """Single chunk from streaming /api/chat. Final chunk has done=True."""

    message: ChatMessage | None = None


class InferenceTelemetry(BaseModel):
    """Telemetry payload for atlas.events. Durations in MILLISECONDS (converted from ns)."""

    model: str
    prompt_eval_count: int | None = None
    eval_count: int | None = None
    total_duration_ms: float | None = None
    load_duration_ms: float | None = None
    prompt_eval_duration_ms: float | None = None
    eval_duration_ms: float | None = None
    status: str = "success"  # 'success' | 'timeout' | 'error'
    fallback_chain: list[str] = Field(default_factory=list)
    endpoint: str = ""
    error: str | None = None
