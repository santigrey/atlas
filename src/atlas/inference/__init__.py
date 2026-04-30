"""Atlas Goliath inference layer.

Goliath Ollama LAN endpoint: http://192.168.1.20:11434 (verified Day 75).
Models available: qwen2.5:72b (primary), deepseek-r1:70b, llama3.1:70b.

Token telemetry logged to atlas.events (source='atlas.inference').
Durations stored in MILLISECONDS (converted from Ollama nanosecond convention).
No prompt or response content captured to atlas.events -- telemetry only.
"""

from atlas.inference.client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL_CHAIN,
    DEFAULT_TIMEOUT,
    GoliathClient,
    MODEL_DEEPSEEK_70B,
    MODEL_LLAMA_70B,
    MODEL_QWEN_72B,
    get_client,
)
from atlas.inference.models import (
    ChatChunk,
    ChatMessage,
    ChatResponse,
    GenerateChunk,
    GenerateResponse,
    InferenceTelemetry,
)
from atlas.inference.telemetry import build_telemetry, log_inference_event

__all__ = [
    "ChatChunk",
    "ChatMessage",
    "ChatResponse",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL_CHAIN",
    "DEFAULT_TIMEOUT",
    "GenerateChunk",
    "GenerateResponse",
    "GoliathClient",
    "InferenceTelemetry",
    "MODEL_DEEPSEEK_70B",
    "MODEL_LLAMA_70B",
    "MODEL_QWEN_72B",
    "build_telemetry",
    "get_client",
    "log_inference_event",
]
