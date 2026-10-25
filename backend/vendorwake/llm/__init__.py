"""Model adapters. Business logic depends on the StructuredLLM protocol, never on a provider SDK."""

from vendorwake.llm.errors import LLMError, LLMOutputError, LLMRequestError, RateLimitedError
from vendorwake.llm.openai_compatible import OpenAICompatibleLLM, StructuredMode

__all__ = [
    "LLMError",
    "LLMOutputError",
    "LLMRequestError",
    "OpenAICompatibleLLM",
    "RateLimitedError",
    "StructuredMode",
]
